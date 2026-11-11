import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import data_skills_ru
from data_skills_ru import Classifier, classify
from data_skills_ru.api import DATA, COMPONENTS
from data_skills_ru.cli import run


ROOT = Path(__file__).resolve().parents[1]


def read_flags(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [{"identifier": row["identifier"], **{c: int(row[c]) for c in COMPONENTS}}
            for row in csv.DictReader(handle, delimiter=";")]


class DictionaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.classifier = Classifier()
        cls.inputs = [json.loads(line) for line in (ROOT / "examples/input.jsonl").read_text(encoding="utf-8-sig").splitlines() if line.strip()]
        cls.expected = read_flags(ROOT / "examples/expected-flags.csv")

    def test_all_documentation_examples_match_frozen_answers(self):
        self.assertEqual(self.classifier.classify(self.inputs).flags, self.expected)

    def test_core_and_dictionary_match_release_fingerprints(self):
        revision = json.loads((DATA / "revision.json").read_text(encoding="utf-8"))
        engine = Path(data_skills_ru.__file__).with_name("_engine.py")
        self.assertEqual(hashlib.sha256(engine.read_bytes()).hexdigest(), revision["engine_sha256"])
        self.assertEqual(hashlib.sha256((DATA / "lexicon.csv").read_bytes()).hexdigest(), revision["lexicon_sha256"])
        self.assertEqual(len(self.classifier.lexicon), 222)

    def test_union_and_repeated_mentions_do_not_multiply_flags(self):
        result = self.classifier.classify([
            {"identifier": "single", "requirements_qualifications": "Знание SQL"},
            {"identifier": "repeated", "requirements_qualifications": "Знание SQL, SQL, SQL"},
        ])
        for row in result.flags:
            self.assertEqual(row["AD"], int(any(row[c] for c in "ABCD")))
            self.assertEqual(row["D"], 1)
        self.assertEqual({c: result.flags[0][c] for c in COMPONENTS},
                         {c: result.flags[1][c] for c in COMPONENTS})

    def test_accepted_and_rejected_evidence_remains_distinguishable(self):
        rows = [{"identifier": "positive", "requirements_qualifications": "ЗНАНИЕ SQL"},
                {"identifier": "training", "responsibilities": "Обучим SQL с нуля"}]
        result = self.classifier.classify(rows, evidence=True)
        self.assertEqual(result.flags[0]["AD"], 1)
        self.assertEqual(result.flags[1]["AD"], 0)
        self.assertTrue(any(e["accepted"] and e["identifier"] == "positive" for e in result.evidence))
        self.assertTrue(any(not e["accepted"] and e["identifier"] == "training" for e in result.evidence))
        inputs = {r["identifier"]: r for r in rows}
        for item in result.evidence:
            text = inputs[item["identifier"]][item["field"]].lower()
            self.assertEqual(text[item["start"]:item["end"]], item["quote"])

    def test_invalid_ids_types_and_duplicates_are_rejected(self):
        for rows in ([{}], [{"identifier": " "}], [{"identifier": 2}],
                     [{"identifier": "id", "title": None}], [{"identifier": "id", "title": ["SQL"]}],
                     [{"identifier": "id"}, {"identifier": "id"}], [], ["not an object"]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                self.classifier.classify(rows)

    def test_missing_text_fields_and_extra_metadata(self):
        row = classify([{"identifier": "empty", "employer": "Python SQL", "title": ""}]).flags[0]
        self.assertTrue(all(row[c] == 0 for c in COMPONENTS))

    def test_important_scope_and_context_cases(self):
        rows = [
            {"identifier": "medical", "title": "Медицинский статистик"},
            {"identifier": "programmer", "title": "Программист"},
            {"identifier": "negative", "requirements_qualifications": "SQL не требуется"},
            {"identifier": "crossfield", "responsibilities": "Анализ данных", "requirements_qualifications": "Знание SQL"},
        ]
        flags = {r["identifier"]: r for r in classify(rows).flags}
        self.assertEqual(flags["medical"]["C"], 1)
        self.assertEqual(flags["programmer"]["E"], 1)
        self.assertEqual(flags["negative"]["AD"], 0)
        self.assertEqual((flags["crossfield"]["C"], flags["crossfield"]["D"]), (1, 1))

    def test_custom_dictionary_is_explicit_and_fingerprinted(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "custom.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, delimiter=";", fieldnames=["term_id", "component", "pattern", "fields", "requires", "provenance"])
                writer.writeheader()
                writer.writerow(dict(term_id="custom-1", component="A", pattern="demo_competence", fields="title", requires="none", provenance="AUTHOR"))
            row = Classifier(path).classify([{"identifier": "custom", "title": "demo_competence"}]).flags[0]
            self.assertEqual((row["A"], row["AD"]), (1, 1))

    def test_lexical_offsets_unicode_boundaries_and_repeated_matches(self):
        result = self.classifier.classify([
            {"identifier": "unicode", "requirements_qualifications": "Знание SQL, SQL\nSQL"},
            {"identifier": "ascii-boundary", "requirements_qualifications": "Знание sqlя"},
            {"identifier": "nonsplitting", "requirements_qualifications": "Знание machine\u00a0learning"},
        ], evidence=True)
        positions = [(item["start"], item["end"]) for item in result.evidence
            if item["identifier"] == "unicode" and item["term_id"] == "ND002"]
        self.assertEqual(positions, [(7, 10), (12, 15), (16, 19)])
        self.assertEqual(result.flags[1]["D"], 1)  # RE2 word boundaries use ASCII.
        self.assertEqual(result.flags[2]["A"], 0)  # RE2 \s does not include NBSP.

    def test_invalid_and_empty_regex_fail_explicitly(self):
        for pattern in ("(", "", "a*", r"(?<=word)token"):
            with self.subTest(pattern=pattern), self.assertRaises(ValueError):
                data_skills_ru._engine.lexical_pattern(pattern)


class CommandLineTests(unittest.TestCase):
    def test_jsonl_and_csv_produce_the_same_labels(self):
        inputs = [json.loads(line) for line in (ROOT / "examples/input.jsonl").read_text(encoding="utf-8-sig").splitlines() if line.strip()]
        expected = read_flags(ROOT / "examples/expected-flags.csv")
        columns = ["identifier", "title", "requirements_qualifications", "responsibilities"]
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            csv_path = root / "input.csv"
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=columns, delimiter=",")
                writer.writeheader()
                writer.writerows({key: row.get(key, "") for key in columns} for row in inputs)
            for index, (source, delimiter) in enumerate(((ROOT / "examples/input.jsonl", ";"), (csv_path, ","))):
                output = root / f"out-{index}"
                report = run(source, output, delimiter=delimiter, evidence=True, batch_size=5)
                self.assertEqual(report["status"], "complete")
                self.assertEqual(report["records"], 14)
                self.assertEqual(read_flags(output / "flags.csv"), expected)
                self.assertTrue((output / "evidence.csv").is_file())
                for filename, value in report["output_hashes"].items():
                    self.assertEqual(hashlib.sha256((output / filename).read_bytes()).hexdigest(), value)

    @unittest.skipUnless(importlib.util.find_spec("duckdb"), "Install .[parquet] to test Parquet reading")
    def test_optional_parquet_produces_the_same_labels(self):
        import duckdb
        inputs = [json.loads(line) for line in (ROOT / "examples/input.jsonl").read_text(encoding="utf-8-sig").splitlines() if line.strip()]
        columns = ["identifier", "title", "requirements_qualifications", "responsibilities"]
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source = root / "input.parquet"
            with duckdb.connect() as con:
                con.execute("CREATE TABLE vacancies(identifier VARCHAR,title VARCHAR,requirements_qualifications VARCHAR,responsibilities VARCHAR)")
                con.executemany("INSERT INTO vacancies VALUES (?,?,?,?)", [[row.get(c, "") for c in columns] for row in inputs])
                con.sql("SELECT * FROM vacancies").write_parquet(str(source))
            output = root / "out"
            report = run(source, output, evidence=True, batch_size=5)
            self.assertEqual(read_flags(output / "flags.csv"), read_flags(ROOT / "examples/expected-flags.csv"))
            self.assertEqual(report["parquet_reader"]["name"], "duckdb")

    def test_core_cli_does_not_import_duckdb(self):
        code = """
import sys
sys.modules['duckdb'] = None
from data_skills_ru import classify
from data_skills_ru.cli import run
assert classify([{'identifier':'sql', 'title':'SQL'}]).flags[0]['D'] == 1
run(sys.argv[1], sys.argv[2], evidence=True)
assert sys.modules['duckdb'] is None
"""
        with tempfile.TemporaryDirectory() as name:
            result = subprocess.run([sys.executable, "-c", code, str(ROOT / "examples/input.jsonl"), str(Path(name) / "out")], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_parquet_extra_reports_install_command(self):
        from data_skills_ru.cli import read_records
        with patch.dict(sys.modules, {"duckdb": None}):
            with self.assertRaisesRegex(ValueError, r"pip install.*parquet"):
                list(read_records(Path("unused.parquet"), "parquet", ";"))

    def test_duplicates_across_batches_never_create_a_completion_marker(self):
        with tempfile.TemporaryDirectory() as name:
            source, output = Path(name) / "input.jsonl", Path(name) / "output"
            source.write_text('{"identifier":"a"}\n{"identifier":"a"}\n', encoding="utf-8")
            with self.assertRaises(ValueError):
                run(source, output, batch_size=1)
            self.assertFalse((output / "completed.json").exists())
            self.assertEqual(json.loads((output / "failed.json").read_text(encoding="utf-8"))["status"], "failed")

    def test_old_outputs_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as name:
            output = Path(name) / "output"
            output.mkdir()
            sentinel = output / "keep.txt"
            sentinel.write_text("keep", encoding="utf-8")
            with self.assertRaises(ValueError):
                run(ROOT / "examples/input.jsonl", output)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_invalid_json_empty_input_and_invalid_csv_fail(self):
        for body, suffix in (("not-json", ".jsonl"), ("\n", ".jsonl"), ("title\nSQL\n", ".csv")):
            with self.subTest(body=body), tempfile.TemporaryDirectory() as name:
                source, output = Path(name) / ("input" + suffix), Path(name) / "out"
                source.write_text(body, encoding="utf-8")
                with self.assertRaises(ValueError):
                    run(source, output)
                self.assertFalse((output / "completed.json").exists())

    def test_public_entrypoint_and_version(self):
        result = subprocess.run([sys.executable, "-m", "data_skills_ru", "--version"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(data_skills_ru.__version__, result.stdout)


class PublicationTests(unittest.TestCase):
    def test_public_metadata_contains_no_development_history(self):
        paths = [ROOT / "README.md", ROOT / "CHANGELOG.md", DATA / "revision.json", DATA / "lexicon.csv",
                 DATA / "source-codes.csv", ROOT / "validation/design.json", ROOT / "data_skills_ru/_engine.py"]
        for path in paths:
            text = path.read_text(encoding="utf-8-sig")
            self.assertNotRegex(text, r"0\.4\.\d|v04[0-9]|PROJECT_|PILOT_VALIDATION|development-not-release|title-rollback|_r[345]_general_reason", str(path))

    def test_every_dictionary_source_code_is_documented(self):
        with (DATA / "source-codes.csv").open(encoding="utf-8-sig", newline="") as handle:
            codes = {row["code"]: int(row["current_rows"]) for row in csv.DictReader(handle, delimiter=";")}
        from collections import Counter
        counts = Counter(code for row in Classifier().lexicon for code in row["provenance"].split("|"))
        self.assertEqual(counts, codes)

    def test_local_documentation_links_resolve(self):
        files = [ROOT / "README.md", ROOT / "LICENSE.md", *list((ROOT / "docs").glob("*.md")),
            *list((ROOT / "examples").glob("*.md")), *list((ROOT / "skills").rglob("*.md"))]
        for path in files:
            for target in re.findall(r"\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
                if not target.startswith(("http:", "https:", "#")):
                    self.assertTrue((path.parent / target).exists(), msg=f"{path.name}: {target}")

    @unittest.skipUnless((ROOT / "RELEASE.json").exists(), "Manifest is generated at final packaging")
    def test_release_manifest_matches_every_published_file(self):
        receipt = json.loads((ROOT / "RELEASE.json").read_text(encoding="utf-8"))
        self.assertFalse(receipt["individual_labels_or_corpus_included"])
        for filename, expected in receipt["file_hashes"].items():
            self.assertEqual(hashlib.sha256((ROOT / filename).read_bytes()).hexdigest(), expected, filename)


if __name__ == "__main__":
    unittest.main()
