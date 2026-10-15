"""Deterministic candidate selection, not diagnosis or calibrated confidence."""

import heapq
import re
from dataclasses import dataclass, field

from ci_investigator.evidence_provenance import SanitizedSource, stable_evidence_id
from ci_investigator.evidence_schemas import PreparationLimits
from ci_investigator.schemas import Evidence

MAX_ANCHORS_PER_SOURCE = 512
MAX_TRACEBACK_LINES = 200
_EXCEPTION = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{0,127}(?:Error|Exception)(?::|$)")
_MISSING_MODULE = re.compile(r"No module named ['\"]([A-Za-z_][A-Za-z0-9_.]{0,127})['\"]")
_TEST_OUTCOME = re.compile(r"(?:^|\s)(?:FAILED|ERROR)\s+\S")
_PYTEST_ASSERTION = re.compile(r"(?:^|\s)E[ \t]+assert\b")
_EXIT = re.compile(
    r"(?:Process completed with exit code [1-9]|exited? with (?:code|status) [1-9])", re.I
)
_INSTALL = re.compile(
    r"(?:no matching distribution|could not find a version|failed building wheel|"
    r"could not build wheels|resolutionimpossible|subprocess-exited-with-error|"
    r"metadata-generation-failed|dependencyconflict|failed to install)",
    re.I,
)
_WORKFLOW_COMMAND = re.compile(
    r"(?:\brun:|setup-python|python-version:|working-directory:|pip install|uv sync)", re.I
)
_DEPENDENCY_CONFIG = re.compile(
    r"(?:dependencies|install_requires|python_requires|requires-python|extras_require|^\s*deps\s*=)",
    re.I,
)
_TEST_CONFIG = re.compile(
    r"(?:pytest|addopts|testpaths|pythonpath|filterwarnings|markers|^\s*commands\s*=)", re.I
)


@dataclass
class FailureIndicators:
    dependency: bool = False
    testing: bool = False
    modules: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class _Window:
    source_index: int
    start: int
    end: int
    anchors: tuple[tuple[int, int], ...]  # (line, relevance priority), not confidence

    @property
    def priority(self) -> int:
        return max((score for _, score in self.anchors), default=0)


@dataclass(frozen=True)
class ExtractionResult:
    evidence: list[Evidence]
    has_failure_detail: bool
    omitted: bool


def _log_score(line: str, steps: tuple[str, ...], indicators: FailureIndicators) -> int:
    lowered = line.lower()
    if "ModuleNotFoundError" in line or "ImportError" in line:
        indicators.dependency = True
        match = _MISSING_MODULE.search(line)
        if match and len(indicators.modules) < 128:
            indicators.modules.add(match.group(1).lower())
        return 120
    if "AssertionError" in line or _TEST_OUTCOME.search(line) or _PYTEST_ASSERTION.search(line):
        indicators.testing = True
        return 110
    if _INSTALL.search(line) or (
        "error:" in lowered
        and any(word in lowered for word in ("install", "wheel", "dependency", "requirement"))
    ):
        indicators.dependency = True
        return 110
    if _EXCEPTION.search(line):
        return 100
    if _EXIT.search(line):
        return 10
    if "::error::" in line or "##[error]" in line:
        return 90
    if "Traceback (most recent call last)" in line:
        return 30
    if "short test summary info" in line or ("failures" in line.lower() and "==" in line):
        indicators.testing = True
        return 30
    if any(step.lower() in lowered for step in steps if step):
        return 20
    return 0


def _config_score(source: SanitizedSource, line: str, indicators: FailureIndicators) -> int:
    if source.path is None:
        return 0
    if source.path.startswith(".github/workflows/"):
        return 60 if _WORKFLOW_COMMAND.search(line) else 0
    if indicators.dependency:
        if (
            source.path.startswith("requirements")
            and line.strip()
            and not line.lstrip().startswith("#")
        ):
            return 70
        if _DEPENDENCY_CONFIG.search(line):
            return 70
        if any(module in line.lower() for module in indicators.modules):
            return 65
    if indicators.testing and _TEST_CONFIG.search(line):
        return 60
    return 0


def _merge(windows: list[_Window]) -> list[_Window]:
    merged: list[_Window] = []
    for window in sorted(windows, key=lambda item: (item.start, item.end)):
        if merged and window.start <= merged[-1].end + 1:
            last = merged.pop()
            merged.append(
                _Window(
                    last.source_index,
                    last.start,
                    max(last.end, window.end),
                    tuple(sorted(set(last.anchors + window.anchors))),
                )
            )
        else:
            merged.append(window)
    return merged


def _split(source: SanitizedSource, window: _Window, cap: int) -> tuple[list[_Window], bool]:
    result: list[_Window] = []
    start = window.start
    size = 0
    omitted = False
    for number in range(window.start, window.end + 1):
        length = len(source.lines[number - 1])
        if length > cap or (number > start and size + 1 + length > cap):
            if number > start:
                anchors = tuple(item for item in window.anchors if start <= item[0] < number)
                result.append(_Window(window.source_index, start, number - 1, anchors))
            start, size = number, 0
        if length > cap:
            start = number + 1
            omitted = True
        else:
            size += length + int(number > start)
    if start <= window.end:
        anchors = tuple(item for item in window.anchors if start <= item[0] <= window.end)
        result.append(_Window(window.source_index, start, window.end, anchors))
    return result, omitted


def _fit(source: SanitizedSource, window: _Window, cap: int) -> _Window | None:
    if len(source.excerpt(window.start, window.end)) <= cap:
        return window
    for anchor, _ in sorted(window.anchors, key=lambda item: (-item[1], item[0])):
        if len(source.lines[anchor - 1]) > cap:
            continue
        start = end = anchor
        size = len(source.lines[anchor - 1])
        while start > window.start or end < window.end:
            changed = False
            if start > window.start and size + 1 + len(source.lines[start - 2]) <= cap:
                start -= 1
                size += 1 + len(source.lines[start - 1])
                changed = True
            if end < window.end and size + 1 + len(source.lines[end]) <= cap:
                size += 1 + len(source.lines[end])
                end += 1
                changed = True
            if not changed:
                break
        return _Window(
            window.source_index,
            start,
            end,
            tuple(item for item in window.anchors if start <= item[0] <= end),
        )
    return None


def extract_evidence(sources: list[SanitizedSource], limits: PreparationLimits) -> ExtractionResult:
    """Select already-sanitized whole-line excerpts, merge overlap, and respect budgets."""
    indicators = FailureIndicators()
    for source in sources:
        for name in source.failed_step_names:
            lowered = name.lower()
            indicators.dependency |= any(
                word in lowered for word in ("install", "pip ", "uv sync", "poetry")
            )
            indicators.testing |= "test" in lowered
    windows: list[_Window] = []
    omitted = False
    # Analyze logs before configurations so dependency/test indicators can guide selection.
    for source_index, source in enumerate(sources):
        if source.source_type != "log":
            continue
        traceback_start: int | None = None
        anchors: list[tuple[int, int, int]] = []
        for number, line in enumerate(source.lines, start=1):
            if "Traceback (most recent call last)" in line:
                traceback_start = number
            score = _log_score(line, source.failed_step_names, indicators)
            if not score:
                continue
            start = max(1, number - limits.context_before)
            if score >= 100 and traceback_start is not None:
                if number - traceback_start < MAX_TRACEBACK_LINES:
                    start = max(1, traceback_start - limits.context_before)
                traceback_start = None
            entry = (score, -number, start)
            if len(anchors) < MAX_ANCHORS_PER_SOURCE:
                heapq.heappush(anchors, entry)
            else:
                omitted = True
                if entry > anchors[0]:
                    heapq.heapreplace(anchors, entry)
        for score, negative_number, start in anchors:
            number = -negative_number
            windows.append(
                _Window(
                    source_index,
                    start,
                    min(len(source.lines), number + limits.context_after),
                    ((number, score),),
                )
            )

    has_log_candidates = bool(windows)
    if has_log_candidates or any(source.failed_step_names for source in sources):
        for source_index, source in enumerate(sources):
            if source.source_type != "config":
                continue
            count = 0
            for number, line in enumerate(source.lines, start=1):
                score = _config_score(source, line, indicators)
                if score:
                    count += 1
                    if count > MAX_ANCHORS_PER_SOURCE:
                        omitted = True
                        continue
                    windows.append(
                        _Window(
                            source_index,
                            max(1, number - limits.context_before),
                            min(len(source.lines), number + limits.context_after),
                            ((number, score),),
                        )
                    )

    chunks: list[_Window] = []
    for source_index, source in enumerate(sources):
        for window in _merge([item for item in windows if item.source_index == source_index]):
            parts, dropped = _split(
                source, window, min(limits.max_excerpt_chars, limits.max_total_chars)
            )
            omitted |= dropped
            chunks.extend(parts)
    ranked = sorted(
        chunks,
        key=lambda item: (
            -(
                item.priority
                if sources[item.source_index].source_type == "log"
                else item.priority // 2
            ),
            item.source_index,
            item.start,
        ),
    )
    selected: list[_Window] = []
    remaining = limits.max_total_chars
    for window in ranked:
        if len(selected) >= limits.max_excerpts or remaining <= 0:
            omitted = True
            break
        source = sources[window.source_index]
        fitted = _fit(source, window, min(remaining, limits.max_excerpt_chars))
        if fitted is None:
            omitted = True
            continue
        omitted |= fitted.start != window.start or fitted.end != window.end
        text = source.excerpt(fitted.start, fitted.end)
        if not text.strip():
            continue
        selected.append(fitted)
        remaining -= len(text)
    evidence: list[Evidence] = []
    has_detail = False
    for window in sorted(selected, key=lambda item: (item.source_index, item.start, item.end)):
        source = sources[window.source_index]
        text = source.excerpt(window.start, window.end)
        evidence.append(
            Evidence(
                evidence_id=stable_evidence_id(source, window.start, window.end, text),
                source_type=source.source_type,
                source_identifier=source.source_identifier,
                start_line=window.start,
                end_line=window.end,
                text=text,
            )
        )
        has_detail |= source.source_type == "log" and window.priority >= 90
    return ExtractionResult(evidence=evidence, has_failure_detail=has_detail, omitted=omitted)
