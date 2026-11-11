"""Classify explicit Russian-language data and IT vacancy requirements."""

from .api import Classifier, ClassificationResult, classify

__version__ = "0.1.0"
__all__ = ["Classifier", "ClassificationResult", "classify", "__version__"]
