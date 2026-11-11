"""Batch CLI with explicit completion markers and non-overwriting outputs."""

import argparse
from contextlib import ExitStack
import csv
import hashlib
import json
from importlib.metadata import version
from pathlib import Path
import sys

from . import __version__, _engine
from .api import Classifier, COMPONENTS, FIELDS, canonical_record


EVIDENCE_FIELDS = ["identifier", "term_id", "component", "field", "start", "end", "quote",
    "matched_text", "requires", "local_reason", "accepted", "reason"]
CONTEXT_FIELDS = ["identifier", "context", "field", "start", "end", "quote"]


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_records(path, kind, delimiter):
    if kind == "jsonl":
        with path.open(encoding="utf-8-sig") as handle:
            for number, line in enumerate(handle, 1):
                if line.strip():
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ValueError(f"Некорректный JSON в строке {number}: {exc.msg}") from exc
    elif kind == "csv":
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=delimiter)
            if not reader.fieldnames or "identifier" not in reader.fieldnames:
                raise ValueError("В CSV нужен столбец identifier; проверьте разделитель.")
            for row in reader:
                if None in row or any(value is None for value in row.values()):
                    raise ValueError("Число ячеек строки CSV отличается от заголовка.")
                yield row
    else:
        try:
            import duckdb
        except ImportError as exc:
            raise ValueError('Для Parquet установите дополнение: python -m pip install ".[parquet]"') from exc
        with duckdb.connect() as con:
            try:
                cursor = con.execute("SELECT * FROM read_parquet(?)", [str(path)])
                names = [item[0] for item in cursor.description]
                while chunk := cursor.fetchmany(1000):
                    for row in chunk:
                        yield dict(zip(names, row))
            except duckdb.Error as exc:
                raise ValueError(f"Не удалось прочитать Parquet: {exc}") from exc


def write_json(path, content):
    path.write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(input_path, out_dir, *, kind=None, delimiter=";", batch_size=1000,
        evidence=False, lexicon=None):
    input_path, out_dir = Path(input_path), Path(out_dir)
    kind = kind or {".jsonl": "jsonl", ".csv": "csv", ".parquet": "parquet"}.get(input_path.suffix.lower())
    if kind not in ("jsonl", "csv", "parquet"):
        raise ValueError("Формат должен быть jsonl, csv или parquet.")
    if batch_size < 1 or len(delimiter) != 1:
        raise ValueError("Размер партии должен быть положительным, разделитель CSV одним символом.")
    if not input_path.is_file():
        raise ValueError("Входной файл не найден.")
    if out_dir.exists():
        raise ValueError("Каталог результатов уже существует; укажите новое имя.")
    classifier = Classifier(lexicon)
    hashes = {"input_sha256": sha256(input_path), "lexicon_sha256": classifier.lexicon_sha256,
        "engine_sha256": sha256(Path(_engine.__file__))}
    out_dir.mkdir(parents=True, exist_ok=False)
    write_json(out_dir / "started.json", {"status": "incomplete", "package_version": __version__, **hashes})
    total, seen, batch = 0, set(), []
    try:
        with ExitStack() as stack:
            fields = {"flags.csv": ["identifier", *COMPONENTS]}
            if evidence:
                fields.update({"evidence.csv": EVIDENCE_FIELDS, "context-evidence.csv": CONTEXT_FIELDS})
            writers = {}
            for filename, columns in fields.items():
                handle = stack.enter_context((out_dir / filename).open("w", encoding="utf-8-sig", newline=""))
                writers[filename] = csv.DictWriter(handle, fieldnames=columns, delimiter=";")
                writers[filename].writeheader()

            def flush():
                nonlocal total
                result = classifier.classify(batch, evidence=evidence)
                writers["flags.csv"].writerows(result.flags)
                if evidence:
                    writers["evidence.csv"].writerows(result.evidence)
                    writers["context-evidence.csv"].writerows(result.context_evidence)
                total += len(batch)
                batch.clear()

            for value in read_records(input_path, kind, delimiter):
                row = canonical_record(value)
                key = row["identifier"]
                if key in seen:
                    raise ValueError(f"Повторный identifier: {key}")
                seen.add(key)
                batch.append(row)
                if len(batch) == batch_size:
                    flush()
            if batch:
                flush()
        if not total:
            raise ValueError("Входной файл не содержит объявлений.")
        if sha256(input_path) != hashes["input_sha256"] or sha256(classifier.lexicon_path) != hashes["lexicon_sha256"]:
            raise ValueError("Входной файл или словарь изменились во время запуска.")
        if sha256(Path(_engine.__file__)) != hashes["engine_sha256"]:
            raise ValueError("Файл классификатора изменился во время запуска.")
        report = {"status": "complete", "package_version": __version__,
            "records": total, "regex_backend": "RE2", "google_re2_version": version("google-re2"), **hashes,
            "output_hashes": {name: sha256(out_dir / name) for name in fields}}
        if kind == "parquet":
            report["parquet_reader"] = {"name": "duckdb", "version": version("duckdb")}
        write_json(out_dir / "completed.json", report)
        return report
    except Exception as exc:
        write_json(out_dir / "failed.json", {"status": "failed", "error_type": type(exc).__name__,
            "message": str(exc), "records_written": total})
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description="Классификация русскоязычных требований к данным и ИТ.")
    parser.add_argument("--version", action="version", version=f"data-skills-ru {__version__}")
    parser.add_argument("--input", required=True, type=Path, help="JSONL, CSV или Parquet")
    parser.add_argument("--out-dir", required=True, type=Path, help="Новый каталог результатов")
    parser.add_argument("--format", choices=("jsonl", "csv", "parquet"), dest="kind")
    parser.add_argument("--delimiter", default=";", help="Разделитель входного CSV; по умолчанию ;")
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--evidence", action="store_true", help="Сохранить основания и отклонённые совпадения")
    parser.add_argument("--lexicon", type=Path, help="Пользовательский lexicon.csv вместо штатного")
    args = parser.parse_args(argv)
    try:
        report = run(args.input, args.out_dir, kind=args.kind, delimiter=args.delimiter,
            batch_size=args.batch_size, evidence=args.evidence, lexicon=args.lexicon)
    except (ValueError, OSError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": report["status"], "records": report["records"],
        "package_version": report["package_version"]}, ensure_ascii=False))
    return 0
