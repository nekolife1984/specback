#!/usr/bin/env python3
"""
specback-health.py — Spec health report / reliability scorecard (Issue #268).

Aggregates existing specback outputs into a per-chapter and overall "spec
health score" rendered as a single Markdown scorecard:

    - chapter files (drafts/ and output-dir): confidence labels
      (🟢 VERIFIED / 🟡 INFERRED / 🔴 ASSUMED and ``<!-- CONFIDENCE: ... -->``),
      REF citations (``<!-- REF: ... -->``), unresolved markers
      (``<!-- BLOCKED:`` / ``<!-- ASK SME`` / ``<!-- ASSUMED``), all counted
      OUTSIDE code fences
    - questions.json           open-question ratio
    - trace.json               MECE coverage (derived from the
                               ``source_units_*`` keys build-trace.py writes)
    - drift-report.json        drift state (read from output-dir first, then
                               specback-dir — detect-drift.py writes it to
                               output-dir)
    - state.json               current phase
    - coverage-check.py        coverage rate + gate failures (JSON output;
                               falls back to N/A when it cannot run)

Usage
-----
    python specback-health.py --specback-dir .specback
    python specback-health.py --specback-dir .specback --json
    python specback-health.py --specback-dir .specback --min-health-score 70
    python specback-health.py --specback-dir .specback --assumed-ratio-threshold 0.25

Exit codes
----------
    0 — report generated (and score >= ``--min-health-score`` when given)
    1 — specback dir missing, no chapter files found, or invalid gate values
    2 — overall score below ``--min-health-score``
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from common import reject_nonfinite, utcnow_iso
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0.0"

# Ratings (>= boundaries)
RATING_A = 90
RATING_B = 75
RATING_C = 60

DEFAULT_ASSUMED_THRESHOLD = 0.3
DEFAULT_MIN_SCORE = 70.0

# Confidence label markers (outside code fences only).
# Word-boundary regexes avoid false positives like "UNVERIFIED" / "UNASSUMED".
WORD_VERIFIED = re.compile(r"\bVERIFIED\b")
WORD_INFERRED = re.compile(r"\bINFERRED\b")
WORD_ASSUMED = re.compile(r"\bASSUMED\b")
COMMENT_VERIFIED = "<!-- CONFIDENCE: verified"
COMMENT_INFERRED = "<!-- CONFIDENCE: inferred"
COMMENT_ASSUMED = "<!-- CONFIDENCE: assumed"
UNRESOLVED_MARKERS = ("<!-- BLOCKED:", "<!-- ASK SME", "<!-- ASSUMED")
REF_PATTERN = "<!-- REF:"

# Reserved files are not spec chapters; they would skew the scorecard
# (traceability.md carries many REFs, metadata carries none).
RESERVED_FILES = ("00-metadata.md", "99-unresolved.md", "traceability.md")

# Scoring weights
W_COVERAGE = 0.30
W_MECE = 0.20
W_ASSUMED = 0.20
W_QUESTIONS = 0.15
W_CHAPTERS = 0.15

MAX_INPUT_BYTES = 50 * 1024 * 1024  # 50 MiB guard against unbounded reads
MAX_JSON_BYTES = 5 * 1024 * 1024  # 5 MiB for auxiliary JSON artifacts


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


class ChapterMetric:
    """Per-chapter scanned metrics."""

    def __init__(self, file: str) -> None:
        self.file = file
        self.body_lines = 0
        self.refs = 0
        self.verified = 0
        self.inferred = 0
        self.assumed = 0
        self.unresolved = 0
        self.unclosed_fence = False

    @property
    def total_labels(self) -> int:
        return self.verified + self.inferred + self.assumed

    @property
    def assumed_ratio(self) -> float:
        if self.total_labels == 0:
            # No evidence is NOT "fully verified" — treat unlabeled chapters
            # as assumed (max assumption penalty) so they surface for
            # refinement instead of silently scoring 100.
            return 1.0
        return self.assumed / self.total_labels

    @property
    def ref_density(self) -> float:
        return self.refs / max(self.body_lines, 1)

    def score(self) -> int:
        """Per-chapter health score (0-100). See docstring formula."""
        value = 100
        value -= round(self.assumed_ratio * 50)
        value -= min(self.unresolved, 10) * 5
        if self.body_lines < 10:
            value -= 20
        value += int(min(self.ref_density * 200, 10))
        return max(0, min(100, value))

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "score": self.score(),
            "body_lines": self.body_lines,
            "refs": self.refs,
            "verified": self.verified,
            "inferred": self.inferred,
            "assumed": self.assumed,
            "unresolved": self.unresolved,
            "assumed_ratio": round(self.assumed_ratio, 4),
            "unclosed_fence": self.unclosed_fence,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _as_finite_number(value: Any) -> float | None:
    """Coerce a JSON value to a finite float; None for bool/non-number/NaN."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    f = float(value)
    return f if math.isfinite(f) else None


def read_text_bounded(path: Path, max_bytes: int = MAX_INPUT_BYTES) -> str:
    """Read a regular file with a byte cap; reject special files / symlinks.

    ``is_file()`` is False for FIFOs, sockets and devices, which closes the
    hang vector (a hostile repo could ship a FIFO at ``questions.json`` or a
    symlink to ``/dev/zero``). ``O_NOFOLLOW`` additionally rejects a symlinked
    final component where the platform supports it. Decoding uses
    ``errors="replace"`` so a non-UTF-8 chapter degrades instead of crashing.
    """
    if not path.is_file():
        raise OSError(f"not a regular file: {path}")
    flags = getattr(os, "O_NOFOLLOW", 0) | os.O_RDONLY
    fd = os.open(path, flags)
    try:
        with os.fdopen(fd, "rb") as f:
            data = f.read(max_bytes + 1)
    finally:
        fd = -1
    if len(data) > max_bytes:
        raise ValueError(f"{path}: exceeds {max_bytes} bytes")
    return data.decode("utf-8", errors="replace")


def load_json(path: Path) -> Any:
    """Read and parse ``path``; raises OSError / ValueError on failure.

    The bounded read also closes the stat-then-read TOCTOU: the byte cap is
    enforced on the bytes actually read, not on a prior stat().
    """
    return json.loads(
        read_text_bounded(path, max_bytes=MAX_JSON_BYTES),
        parse_constant=reject_nonfinite,
    )


def scan_chapter(path: Path, metric: ChapterMetric) -> None:
    """Scan one markdown chapter, counting markers OUTSIDE code fences."""
    try:
        text = read_text_bounded(path)
    except (OSError, ValueError):
        return  # unscannable chapter — skip (N/A semantics)
    in_code = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        if not stripped:
            continue
        metric.body_lines += 1
        metric.verified += (
            line.count("🟢") + len(WORD_VERIFIED.findall(line)) + line.count(COMMENT_VERIFIED)
        )
        metric.inferred += (
            line.count("🟡") + len(WORD_INFERRED.findall(line)) + line.count(COMMENT_INFERRED)
        )
        metric.assumed += (
            line.count("🔴") + len(WORD_ASSUMED.findall(line)) + line.count(COMMENT_ASSUMED)
        )
        metric.refs += line.count(REF_PATTERN)
        for marker in UNRESOLVED_MARKERS:
            metric.unresolved += line.count(marker)
    if in_code:
        metric.unclosed_fence = True


def collect_chapters(specback_dir: Path, output_dir: Path) -> list[ChapterMetric]:
    """Scan drafts/ (preferred) plus output-dir chapters; drafts win on name.

    ``health-report.md`` and the reserved files (``00-metadata.md`` /
    ``99-unresolved.md`` / ``traceability.md``) are excluded: the report never
    treats its own previous output as a spec chapter, and reserved files are
    not chapters (they would skew the scorecard).
    """
    seen: set[str] = set()
    metrics: list[ChapterMetric] = []
    for directory in (specback_dir / "drafts", output_dir):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            if path.name in seen:
                continue
            if path.name == "health-report.md" or path.name in RESERVED_FILES:
                continue
            seen.add(path.name)
            metric = ChapterMetric(path.name)
            scan_chapter(path, metric)
            metrics.append(metric)
    return metrics


def load_questions(specback_dir: Path) -> dict[str, Any]:
    """questions.json -> {total, open, open_ratio} or N/A (None)."""
    path = specback_dir / "questions.json"
    if not path.is_file():
        return {"total": None, "open": None, "open_ratio": None}
    try:
        data = load_json(path)
    except (OSError, ValueError):
        return {"total": None, "open": None, "open_ratio": None}
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict) and isinstance(data.get("questions"), list):
        items = data["questions"]
    else:
        return {"total": None, "open": None, "open_ratio": None}
    total = len(items)
    open_count = sum(1 for q in items if isinstance(q, dict) and q.get("status") == "open")
    return {
        "total": total,
        "open": open_count,
        "open_ratio": (open_count / total) if total else 0.0,
    }


def load_trace(specback_dir: Path, output_dir: Path) -> dict[str, Any]:
    """MECE coverage derived from trace.json's ``source_units_*`` keys.

    build-trace.py does NOT write a ``mece_coverage_rate`` key; the rate is
    ``covered / max(total - excluded, 1)`` (same derivation as
    coverage-check.py). The file may live under output-dir (phase-4/7 flow
    writes it there), so both locations are probed.
    """
    for candidate in (specback_dir / "trace.json", output_dir / "trace.json"):
        if candidate.is_file():
            break
    else:
        return {"mece_coverage_rate": None}
    try:
        data = load_json(candidate)
    except (OSError, ValueError):
        return {"mece_coverage_rate": None}
    if not isinstance(data, dict):
        return {"mece_coverage_rate": None}
    total = data.get("source_units_total")
    covered = data.get("source_units_covered")
    excluded = data.get("source_units_excluded")
    if not (
        isinstance(total, int)
        and not isinstance(total, bool)
        and isinstance(covered, int)
        and not isinstance(covered, bool)
        and isinstance(excluded, int)
        and not isinstance(excluded, bool)
    ):
        return {"mece_coverage_rate": None}
    denom = max(total - excluded, 1)
    return {"mece_coverage_rate": min(covered / denom, 1.0)}


def load_drift(specback_dir: Path, output_dir: Path) -> dict[str, Any]:
    """drift-report.json summary or N/A.

    detect-drift.py writes the report to ``{output_dir}/drift-report.json``,
    so output-dir is probed first, specback-dir as fallback.
    """
    for path in (output_dir / "drift-report.json", specback_dir / "drift-report.json"):
        if path.is_file():
            break
    else:
        return {
            "changed_files": None,
            "affected_sections": None,
            "new_uncovered": None,
        }
    try:
        data = load_json(path)
    except (OSError, ValueError):
        return {
            "changed_files": None,
            "affected_sections": None,
            "new_uncovered": None,
        }
    summary = data.get("summary", {}) if isinstance(data, dict) else {}
    return {
        "changed_files": summary.get("changed_files"),
        "affected_sections": summary.get("affected_spec_sections"),
        "new_uncovered": summary.get("new_uncovered_sources"),
    }


def load_phase(specback_dir: Path) -> int | None:
    """state.json current_phase or None."""
    path = specback_dir / "state.json"
    if not path.is_file():
        return None
    try:
        data = load_json(path)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    phase = data.get("current_phase")
    return phase if isinstance(phase, int) and not isinstance(phase, bool) else None


def _target_has_special_files(target: Path) -> bool:
    """True if a chapter dir contains non-regular .md files (FIFO, socket...).

    health itself skips special files (read_text_bounded), but the
    coverage-check subprocess would block reading them — so when the target
    dir holds any, coverage is reported as N/A instead of invoking it.
    """
    if not target.is_dir():
        return False
    return any(not p.is_file() for p in target.glob("*.md"))


def load_coverage(specback_dir: Path, output_dir: Path) -> dict[str, Any]:
    """Run coverage-check.py --output-format json; N/A on any failure.

    The exit code of coverage-check is deliberately ignored: a non-zero exit
    (e.g. gate failures) still carries a usable JSON report on stdout.

    When ``--output-dir`` is not given (the documented default), chapters live
    in ``drafts/`` — coverage-check is pointed at ``{specback-dir}/drafts`` so
    it actually finds them instead of reporting 0.0% on the specback root.
    """
    script = Path(__file__).resolve().parent / "coverage-check.py"
    target = output_dir
    if output_dir == specback_dir:
        target = specback_dir / "drafts"
    if _target_has_special_files(target):
        return {
            "coverage_rate": None,
            "gate_failures": None,
            "available": False,
        }
    cmd = [
        sys.executable,
        str(script),
        "--specback-dir",
        str(specback_dir),
        "--output-dir",
        str(output_dir),
        "--target-dir-for-required",
        str(target),
        "--output-format",
        "json",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if len(proc.stdout) > MAX_JSON_BYTES:
            return {
                "coverage_rate": None,
                "gate_failures": None,
                "available": False,
            }
        data = json.loads(proc.stdout, parse_constant=reject_nonfinite)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {
            "coverage_rate": None,
            "gate_failures": None,
            "available": False,
        }
    if not isinstance(data, dict):
        return {
            "coverage_rate": None,
            "gate_failures": None,
            "available": False,
        }
    gates = data.get("gate_failures")
    return {
        "coverage_rate": _as_finite_number(data.get("coverage_rate")),
        "gate_failures": gates if isinstance(gates, list) else None,
        "available": True,
    }


def rating_for(score: float) -> tuple[str, str]:
    """Return (rating, label) for an overall score."""
    if score >= RATING_A:
        return "A", "納品可能"
    if score >= RATING_B:
        return "B", "軽微な精緻化を推奨"
    if score >= RATING_C:
        return "C", "精緻化が必要"
    return "D", "要再調査"


def overall_score(
    chapters: list[ChapterMetric],
    coverage_rate: float | None,
    mece_rate: float | None,
    assumed_ratio: float,
    questions: dict[str, Any],
) -> float:
    """Weighted average of available metrics, renormalizing missing weights."""
    metrics: list[tuple[float, float]] = []
    if coverage_rate is not None:
        metrics.append((W_COVERAGE, max(0.0, min(100.0, coverage_rate))))
    if mece_rate is not None:
        metrics.append((W_MECE, max(0.0, min(100.0, mece_rate * 100.0))))
    metrics.append((W_ASSUMED, (1.0 - assumed_ratio) * 100.0))
    if questions.get("total") is not None and questions.get("total", 0) > 0:
        open_ratio = float(questions.get("open_ratio") or 0.0)
        metrics.append((W_QUESTIONS, (1.0 - open_ratio) * 100.0))
    if chapters:
        mean = sum(c.score() for c in chapters) / len(chapters)
        metrics.append((W_CHAPTERS, float(mean)))

    total_weight = sum(w for w, _ in metrics)
    if total_weight == 0:
        return 0.0
    return sum(w * v for w, v in metrics) / total_weight


def needs_refinement(chapters: list[ChapterMetric], threshold: float) -> list[ChapterMetric]:
    return [c for c in chapters if c.assumed_ratio > threshold]


def _md_escape(value: Any) -> str:
    """Neutralize markdown/table injection via filenames or report values."""
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _write_report(path: Path, content: str) -> None:
    """Write atomically; refuse to write through a pre-planted symlink.

    A hostile repository can commit ``.specback/health-report.md`` as a
    symlink (e.g. to ``~/.bashrc`` or ``.git/config``). Writing via
    ``O_NOFOLLOW|O_EXCL`` temp file + ``os.replace`` never opens the final
    path for writing, so the destination entry is swapped atomically and a
    symlinked destination is refused up front.
    """
    if path.is_symlink():
        raise OSError(f"refusing to write through symlink: {path}")
    flags = getattr(os, "O_NOFOLLOW", 0) | os.O_WRONLY | os.O_CREAT | os.O_EXCL
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = -1
    try:
        fd = os.open(tmp, flags, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        fd = -1  # ownership moved to fdopen; already closed by the `with`
        os.replace(tmp, path)
    finally:
        if fd != -1:
            os.close(fd)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _fmt(value: Any, suffix: str = "") -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.1f}{suffix}"
    return f"{value}{suffix}"


def render_markdown(
    specback_dir: Path,
    chapters: list[ChapterMetric],
    coverage: dict[str, Any],
    mece_rate: float | None,
    drift: dict[str, Any],
    questions: dict[str, Any],
    phase: int | None,
    overall: float,
    rating: str,
    rating_label: str,
    threshold: float,
) -> str:
    assumed_total = sum(c.assumed for c in chapters)
    labels_total = sum(c.total_labels for c in chapters)
    overall_assumed = assumed_total / labels_total if labels_total else 0.0
    drift_txt = (
        "N/A"
        if drift["changed_files"] is None
        else f"{_md_escape(drift['changed_files'])} changed files, "
        f"{_md_escape(drift['affected_sections'])} affected section(s)"
    )
    questions_txt = (
        "N/A"
        if questions["total"] is None
        else f"{questions['open']} / {questions['total']} "
        f"({questions['open_ratio']:.1%})"
    )
    gate_txt = (
        "N/A"
        if coverage["gate_failures"] is None
        else str(len(coverage["gate_failures"]))
    )

    lines: list[str] = []
    lines.append("# Spec Health Report")
    lines.append("")
    lines.append(f"Generated: {utcnow_iso()}")
    lines.append(f"Specback dir: {specback_dir}")
    lines.append(f"Overall health score: **{overall:.0f} / 100 ({rating}: {rating_label})**")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Chapters scanned | {len(chapters)} |")
    lines.append(f"| Coverage rate | {_fmt(coverage['coverage_rate'], '%')} |")
    lines.append(f"| MECE coverage | {_fmt(mece_rate * 100, '%') if mece_rate is not None else 'N/A'} |")
    lines.append(f"| ASSUMED ratio (overall) | {overall_assumed:.1%} |")
    lines.append(f"| Open questions | {questions_txt} |")
    lines.append(f"| Drift status | {drift_txt} |")
    lines.append(f"| Gate failures | {gate_txt} |")
    lines.append(f"| Current phase | {_fmt(phase)} |")
    lines.append("")
    lines.append("## Per-chapter scorecard")
    lines.append("")
    lines.append("| Chapter | Score | Body lines | REFs | 🟢 | 🟡 | 🔴 | Unresolved | ASSUMED % | Flags |")
    lines.append("|---------|-------|-----------|------|----|----|----|------------|-----------|-------|")
    for c in chapters:
        score = c.score()
        flag = "✅" if score >= 75 else ("⚠️" if score >= 60 else "❌")
        if c.unclosed_fence:
            flag += " 🔓"
        assumed_pct = f"{c.assumed_ratio:.0%}" if c.total_labels else "-"
        lines.append(
            f"| {_md_escape(c.file)} | {score} | {c.body_lines} | {c.refs} | "
            f"{c.verified} | {c.inferred} | {c.assumed} | {c.unresolved} | "
            f"{assumed_pct} | {flag} |"
        )
    lines.append("")
    lines.append("## Needs refinement (Phase 5 suggested)")
    lines.append("")
    offenders = needs_refinement(chapters, threshold)
    if not offenders:
        lines.append("(none)")
    else:
        for c in offenders:
            lines.append(
                f"- {_md_escape(c.file)}: ASSUMED ratio {c.assumed_ratio:.0%} "
                f"(threshold {threshold:.0%}) — strengthen grounding via "
                f"mechanical extraction"
            )
    lines.append("")
    lines.append("## Gate failures (from coverage-check)")
    lines.append("")
    failures = coverage["gate_failures"] or []
    if not failures:
        lines.append("(none)")
    else:
        for f in failures:
            lines.append(f"- {_md_escape(f)}")
    lines.append("")
    return "\n".join(lines)


def render_json(
    specback_dir: Path,
    chapters: list[ChapterMetric],
    coverage: dict[str, Any],
    mece_rate: float | None,
    drift: dict[str, Any],
    questions: dict[str, Any],
    phase: int | None,
    overall: float,
    rating: str,
    rating_label: str,
    threshold: float,
) -> str:
    assumed_total = sum(c.assumed for c in chapters)
    labels_total = sum(c.total_labels for c in chapters)
    overall_assumed = assumed_total / labels_total if labels_total else 0.0
    doc: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utcnow_iso(),
        "specback_dir": str(specback_dir),
        "overall_score": round(overall),
        "rating": rating,
        "rating_label": rating_label,
        "summary": {
            "chapters_scanned": len(chapters),
            "coverage_rate": coverage["coverage_rate"],
            "mece_coverage_rate": mece_rate,
            "assumed_ratio": round(overall_assumed, 4),
            "open_questions": questions["open"],
            "questions_total": questions["total"],
            "open_question_ratio": questions["open_ratio"],
            "drift_changed_files": drift["changed_files"],
            "drift_affected_sections": drift["affected_sections"],
            "drift_new_uncovered": drift["new_uncovered"],
            "gate_failures": coverage["gate_failures"],
            "current_phase": phase,
        },
        "chapters": [c.to_dict() for c in chapters],
        "needs_refinement": [
            c.file for c in needs_refinement(chapters, threshold)
        ],
        "coverage_available": coverage["available"],
    }
    return json.dumps(doc, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="specback health report: per-chapter and overall "
        "reliability scorecard (Issue #268)",
    )
    parser.add_argument(
        "--specback-dir",
        type=Path,
        default=Path(".specback"),
        help="Path to .specback/ directory (default: .specback)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory for the report (default: --specback-dir)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Also write health-report.json",
    )
    parser.add_argument(
        "--min-health-score",
        type=float,
        default=None,
        help="Gate: exit 2 when the overall score is below this value",
    )
    parser.add_argument(
        "--assumed-ratio-threshold",
        type=float,
        default=DEFAULT_ASSUMED_THRESHOLD,
        help=f"Per-chapter ASSUMED ratio warning threshold "
        f"(default: {DEFAULT_ASSUMED_THRESHOLD})",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.min_health_score is not None and not math.isfinite(args.min_health_score):
        print("ERROR: --min-health-score must be a finite number", file=sys.stderr)
        return 1
    threshold = max(0.0, min(1.0, args.assumed_ratio_threshold))
    specback_dir = args.specback_dir.resolve()
    output_dir = (args.output_dir or args.specback_dir).resolve()

    if not specback_dir.is_dir():
        print(
            f"ERROR: specback dir not found: {specback_dir}",
            file=sys.stderr,
        )
        return 1

    chapters = collect_chapters(specback_dir, output_dir)
    if not chapters:
        print(
            "ERROR: no chapter files found under "
            f"{specback_dir / 'drafts'} or {output_dir}",
            file=sys.stderr,
        )
        return 1

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except FileExistsError:
        print(
            f"ERROR: --output-dir exists and is not a directory: {output_dir}",
            file=sys.stderr,
        )
        return 1

    coverage = load_coverage(specback_dir, output_dir)
    mece_rate = load_trace(specback_dir, output_dir)["mece_coverage_rate"]
    drift = load_drift(specback_dir, output_dir)
    questions = load_questions(specback_dir)
    phase = load_phase(specback_dir)

    assumed_total = sum(c.assumed for c in chapters)
    labels_total = sum(c.total_labels for c in chapters)
    overall_assumed = assumed_total / labels_total if labels_total else 0.0

    overall = overall_score(
        chapters,
        coverage["coverage_rate"],
        mece_rate,
        overall_assumed,
        questions,
    )
    rating, rating_label = rating_for(overall)

    md = render_markdown(
        specback_dir,
        chapters,
        coverage,
        mece_rate,
        drift,
        questions,
        phase,
        overall,
        rating,
        rating_label,
        threshold,
    )
    report_path = output_dir / "health-report.md"
    try:
        _write_report(report_path, md)
    except OSError as e:
        print(f"ERROR: cannot write report: {e}", file=sys.stderr)
        return 1
    print(f"Wrote {report_path}")
    print(f"Overall health score: {overall:.0f} / 100 ({rating}: {rating_label})")

    if args.json:
        js = render_json(
            specback_dir,
            chapters,
            coverage,
            mece_rate,
            drift,
            questions,
            phase,
            overall,
            rating,
            rating_label,
            threshold,
        )
        json_path = output_dir / "health-report.json"
        try:
            _write_report(json_path, js)
        except OSError as e:
            print(f"ERROR: cannot write report: {e}", file=sys.stderr)
            return 1
        print(f"Wrote {json_path}")

    if args.min_health_score is not None and overall < args.min_health_score:
        print(
            f"GATE FAILED: overall score {overall:.0f} < "
            f"--min-health-score {args.min_health_score:g}",
            file=sys.stderr,
        )
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
