"""Run after installing the package: python examples/use_api.py."""

import json

from data_skills_ru import Classifier


result = Classifier().classify([
    {"identifier": "api-example", "title": "Аналитик",
     "requirements_qualifications": "Знание SQL", "responsibilities": "Анализ данных"}
], evidence=True)
print(json.dumps(result.flags, ensure_ascii=False, indent=2))
