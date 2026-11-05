"""Static analysis: read an application module with ``ast``, check it, and build its specification.

Depends only on ``core``.
"""

from pyifc.analysis.analyzer import analyze_file, analyze_source
from pyifc.analysis.result import AnalysisResult

__all__ = ["AnalysisResult", "analyze_file", "analyze_source"]
