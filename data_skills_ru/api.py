"""Classify vacancy records using the dictionary and context checks."""

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from . import _engine


DATA = Path(__file__).with_name("data")
COMPONENTS = _engine.COMPONENTS
FIELDS = _engine.FIELDS


@dataclass
class ClassificationResult:
    flags: list[dict]
    evidence: list[dict]
    context_evidence: list[dict]


def canonical_record(record: dict) -> dict:
    if not isinstance(record, dict):
        raise ValueError("Каждое объявление должно быть объектом с полями.")
    identifier = record.get("identifier")
    if not isinstance(identifier, str) or not identifier.strip():
        raise ValueError("identifier должен быть непустой строкой.")
    values = {name: record.get(name, "") for name in FIELDS}
    for name, value in values.items():
        if not isinstance(value, str):
            raise ValueError(f"Поле {name} должно быть строкой; null замените пустой строкой.")
    return {"identifier": identifier, **values}


class Classifier:
    """Load the lexicon once and classify independent batches of records."""

    def __init__(self, lexicon: str | Path | None = None):
        self.lexicon_path = Path(lexicon) if lexicon is not None else DATA / "lexicon.csv"
        self.lexicon = _engine.load_lexicon(self.lexicon_path)
        self.lexicon_sha256 = _engine.digest(self.lexicon_path)

    def classify(self, records: Iterable[dict], *, evidence: bool = False) -> ClassificationResult:
        rows = [canonical_record(row) for row in records]
        flags, matches, contexts = _engine.classify(rows, self.lexicon, collect_evidence=evidence)
        return ClassificationResult(
            flags=[{"identifier": key, **{c: int(value[c]) for c in COMPONENTS}}
                for key, value in flags.items()],
            evidence=matches, context_evidence=contexts)


def classify(records: Iterable[dict], *, evidence: bool = False,
             lexicon: str | Path | None = None) -> ClassificationResult:
    return Classifier(lexicon).classify(records, evidence=evidence)
