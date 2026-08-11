#!/usr/bin/env python3
"""
specback build-search-index.py — query specback-generated JSON artifacts.

Reads source-map.json, trace.json, inventory.json, questions.json, and
drift-report.json from the .specback/ directory and provides structured
search across all of them.

Usage:
    python build-search-index.py [query] [flags]
    python build-search-index.py "User"
    python build-search-index.py --uncovered
    python build-search-index.py --uncovered --role module
    python build-search-index.py --chapter 03-data-model
    python build-search-index.py --questions open
    python build-search-index.py --drift
    python build-search-index.py "payment" --confidence 🔴

Dependencies:
    Python 3.10+ (stdlib only).
"""

from __future__ import annotations

import argparse
import functools
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "0.1.0"

# Max artifact file size accepted (bytes) — guards against multi-GB inputs.
MAX_ARTIFACT_BYTES = 50 * 1024 * 1024  # 50 MiB

# Confidence markers found in spec chapter <!-- REF: ... --> annotations
# Supports both path:line format (<!-- REF: path:file:1-50 --> 🟢) and
# SRC-ID format (<!-- REF: SRC-0001 --> 🟢). Group 1 = REF target, group 2 = marker.
CONFIDENCE_RE = re.compile(r'<!-- REF:\s*(\S+:\d+(?:-\d+)?|SRC-\d+)\s*-->\s*([🟢🟡🔴])')


class SpecbackDataError(Exception):
    """Raised when specback artifacts are missing, unreadable, or invalid.

    The CLI's main() catches this and exits 2; the MCP server catches it and
    returns an isError tool result. Never sys.exit() from data loading — a
    library consumer (e.g. the MCP server) must be able to survive bad data.
    """


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class SearchIndex:
    source_map: dict[str, Any]
    trace: dict[str, Any]
    inventory: dict[str, Any]
    questions: list[dict[str, Any]]
    drift: dict[str, Any] | None
    specback_dir: Path

    def has_source_map(self) -> bool:
        return bool(self.source_map.get("units"))

    def has_trace(self) -> bool:
        return bool(self.trace.get("by_source"))

    def has_inventory(self) -> bool:
        return bool(self.inventory.get("units"))

    def has_questions(self) -> bool:
        return len(self.questions) > 0

    def has_drift(self) -> bool:
        return self.drift is not None


@dataclass
class SourceUnitResult:
    src_id: str
    name: str
    path: str
    line_range: list[int]
    role: str
    language: str
    tier: str
    kind: str
    confidence: str | None  # 🟢/🟡/🔴 from spec chapter REFs
    covered_by_sections: list[dict[str, str]] = field(default_factory=list)
    inventory_items: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def load_json(path: Path, optional: bool = False) -> Any:
    """Load a JSON artifact, raising SpecbackDataError if unusable.

    Raises SpecbackDataError (never sys.exit) so library consumers such as the
    MCP server can return a tool-level error instead of dying. The CLI's
    main() catches it and exits 2, preserving CLI behavior.
    """
    if not path.exists():
        if optional:
            return None
        raise SpecbackDataError(f"{path} not found. Run specback first.")
    if not path.is_file():
        # FIFOs / sockets / devices exist but are not regular files; reading
        # them can hang forever (FIFO) or return garbage.
        raise SpecbackDataError(f"{path} is not a regular file.")
    if path.stat().st_size > MAX_ARTIFACT_BYTES:
        raise SpecbackDataError(f"{path} exceeds {MAX_ARTIFACT_BYTES} bytes.")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SpecbackDataError(f"invalid JSON in {path}: {e}") from e


def build_index(specback_dir: Path) -> SearchIndex:
    """Load all specback artifacts into a SearchIndex."""
    return SearchIndex(
        source_map=load_json(specback_dir / "source-map.json"),
        trace=load_json(specback_dir / "trace.json"),
        inventory=load_json(specback_dir / "inventory.json", optional=True) or {"units": []},
        questions=load_json(specback_dir / "questions.json", optional=True) or [],
        drift=load_json(specback_dir / "drift-report.json", optional=True),
        specback_dir=specback_dir,
    )


# ---------------------------------------------------------------------------
# Confidence extraction from spec chapter files
# ---------------------------------------------------------------------------

def _chapter_files(specback_dir: Path) -> list[Path]:
    """Find spec chapter files (final/*.md or drafts/*.md).

    Skips symlinked directories and files that escape the specback dir, so a
    planted symlink cannot make the search read arbitrary .md trees outside
    the project.
    """
    resolved_sb = specback_dir.resolve()
    candidates: list[Path] = []
    for sub in ("final", "drafts"):
        d = specback_dir / sub
        if d.is_dir() and not d.is_symlink():
            for f in sorted(d.glob("*.md")):
                try:
                    if f.resolve().is_relative_to(resolved_sb):
                        candidates.append(f)
                except OSError:
                    continue
    return candidates


@functools.lru_cache(maxsize=256)
def _read_chapter_text(ch_path: Path) -> str:
    """Read a chapter file with process-lifetime caching.

    Confidence extraction re-scans chapter files on every search request;
    caching avoids re-reading the same files repeatedly. Chapter files change
    rarely during a server session, so staleness is acceptable.
    """
    return ch_path.read_text(encoding="utf-8")


def _extract_confidence(src_id: str, specback_dir: Path) -> str | None:
    """Scan spec chapter files for a REF mentioning src_id and extract its confidence marker."""
    confidence_markers = []
    for ch_path in _chapter_files(specback_dir):
        try:
            text = _read_chapter_text(ch_path)
        except Exception:
            continue
        # Find lines with REFs referencing this SRC-ID
        for line in text.split("\n"):
            if src_id not in line:
                continue
            for m in CONFIDENCE_RE.finditer(line):
                target, marker = m.group(1), m.group(2)
                # SRC-ID REFs must point at this unit; path:line REFs are
                # accepted when the unit id appears on the same line.
                if target.startswith("SRC-"):
                    if target == src_id:
                        confidence_markers.append(marker)
                else:
                    confidence_markers.append(marker)
    if not confidence_markers:
        return None
    # Return the lowest confidence found (🔴 < 🟡 < 🟢)
    if "🔴" in confidence_markers:
        return "🔴"
    if "🟡" in confidence_markers:
        return "🟡"
    return "🟢"


# ---------------------------------------------------------------------------
# Search functions
# ---------------------------------------------------------------------------

def search_by_name(index: SearchIndex, query: str) -> list[SourceUnitResult]:
    """Find source units with name or path matching query (substring, case-insensitive)."""
    query_lower = query.lower()
    results: list[SourceUnitResult] = []
    for unit in index.source_map.get("units", []):
        name = unit.get("name", "")
        path = unit.get("path", "")
        if query_lower not in name.lower() and query_lower not in path.lower():
            continue
        results.append(_build_result(unit, index))
    return results


def find_uncovered(index: SearchIndex) -> list[SourceUnitResult]:
    """Find source units not covered by any spec chapter."""
    uncovered_ids = set(index.trace.get("uncovered_units", []))
    results: list[SourceUnitResult] = []
    for unit in index.source_map.get("units", []):
        if unit["id"] in uncovered_ids:
            results.append(_build_result(unit, index))
    return results


def filter_by_chapter(index: SearchIndex, chapter_slug: str) -> list[SourceUnitResult]:
    """Find source units covered by a specific chapter slug."""
    by_section = index.trace.get("by_section", {})
    matching_ids: set[str] = set()
    for section_key in by_section:
        file_part = section_key.split("::")[0]
        if chapter_slug in file_part:
            matching_ids.update(by_section[section_key])

    results: list[SourceUnitResult] = []
    for unit in index.source_map.get("units", []):
        if unit["id"] in matching_ids:
            results.append(_build_result(unit, index))
    return results


def filter_by_role(index: SearchIndex, role: str) -> list[SourceUnitResult]:
    """Find source units with a specific role (exact match, case-insensitive)."""
    role_lower = role.lower()
    results: list[SourceUnitResult] = []
    for unit in index.source_map.get("units", []):
        if unit.get("role", "").lower() == role_lower:
            results.append(_build_result(unit, index))
    return results


def get_questions(index: SearchIndex, mode: str = "open") -> list[dict[str, Any]]:
    """Get questions, optionally filtered by status."""
    questions = index.questions
    if mode == "open":
        questions = [q for q in questions if q.get("status") == "open"]
    return questions


def get_drift_summary(index: SearchIndex) -> dict[str, Any] | None:
    """Get the drift report, if available."""
    return index.drift


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_result(unit: dict[str, Any], index: SearchIndex) -> SourceUnitResult:
    """Build a SourceUnitResult from a source-map unit and the search index."""
    src_id = unit["id"]
    trace_info = index.trace.get("by_source", {}).get(src_id, {})
    inv_items = [
        inv for inv in index.inventory.get("units", [])
        if src_id in inv.get("related_source_ids", [])
    ]
    return SourceUnitResult(
        src_id=src_id,
        name=unit.get("name", ""),
        path=unit.get("path", ""),
        line_range=unit.get("line_range", []),
        role=unit.get("role", ""),
        language=unit.get("language", ""),
        tier=unit.get("tier", ""),
        kind=unit.get("kind", ""),
        confidence=_extract_confidence(src_id, index.specback_dir),
        covered_by_sections=trace_info.get("covered_by_sections", []),
        inventory_items=inv_items,
    )


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------

def format_text_results(results: list[SourceUnitResult], title: str) -> str:
    """Format source unit results as human-readable text."""
    lines: list[str] = []
    if not results:
        lines.append(f"🔍 {title}")
        lines.append("  (no results found)")
        return "\n".join(lines)

    lines.append(f"🔍 {title} — {len(results)} result(s)")
    lines.append("")

    for r in results:
        lr = r.line_range
        lr_str = f":{lr[0]}-{lr[1]}" if isinstance(lr, list) and len(lr) >= 2 else ""
        confidence = r.confidence or ""
        lines.append(f"  {r.src_id} {confidence} {r.path}{lr_str}")
        if r.name:
            lines.append(f"    → {r.name} ({r.role}, {r.language})")
        for sec in r.covered_by_sections:
            lines.append(f"    → 📘 {sec.get('file', '?')} (§{sec.get('section', '?')})")
        for inv in r.inventory_items:
            inv_type = inv.get("type", "")
            inv_name = inv.get("name", "")
            lines.append(f"    → {inv.get('id', '?')}: {inv_name} ({inv_type})")
        lines.append("")

    return "\n".join(lines)


def format_questions_text(questions: list[dict[str, Any]], mode: str) -> str:
    """Format questions as human-readable text."""
    label = "❓ Open questions" if mode == "open" else "❓ All questions"
    lines: list[str] = []
    if not questions:
        lines.append(f"{label}")
        lines.append("  (none)")
        return "\n".join(lines)

    lines.append(f"{label} — {len(questions)}")
    lines.append("")
    for q in questions:
        qid = q.get("id", "?")
        cat = q.get("category", "")
        body = q.get("body", "")
        sev = q.get("severity", "")
        status = q.get("status", "")
        lines.append(f"  {qid} [{cat}] \"{body}\"")
        lines.append(f"    severity: {sev}, status: {status}")
        if q.get("answer"):
            lines.append(f"    answer: {q['answer']}")
        lines.append("")
    return "\n".join(lines)


def format_drift_text(drift: dict[str, Any]) -> str:
    """Format drift report as human-readable text."""
    lines: list[str] = ["🔄 Drift Report"]
    summary = drift.get("summary", {})
    if not summary:
        lines.append("  (insufficient data)")
        return "\n".join(lines)
    lines.append(f"  Changed files: {summary.get('changed_files', 0)}")
    lines.append(f"  Affected spec sections: {summary.get('affected_spec_sections', 0)}")
    lines.append(f"  New uncovered sources: {summary.get('new_uncovered_sources', 0)}")
    lines.append(f"  Deleted references: {summary.get('deleted_sources_with_refs', 0)}")
    lines.append(f"  Unaffected changes: {summary.get('no_impact_changes', 0)}")
    return "\n".join(lines)


def format_results_json(
    results: list[SourceUnitResult],
    questions: list[dict[str, Any]] | None,
    drift: dict[str, Any] | None,
) -> str:
    """Format all output as JSON."""
    output: dict[str, Any] = {}
    if results:
        output["results"] = [
            {
                "src_id": r.src_id,
                "name": r.name,
                "path": r.path,
                "line_range": r.line_range,
                "role": r.role,
                "language": r.language,
                "tier": r.tier,
                "kind": r.kind,
                "confidence": r.confidence,
                "covered_by": [
                    {"file": s["file"], "section": s["section"]}
                    for s in r.covered_by_sections
                ],
                "inventory": [
                    {"id": inv.get("id"), "type": inv.get("type"), "name": inv.get("name")}
                    for inv in r.inventory_items
                ],
            }
            for r in results
        ]
    if questions is not None:
        output["questions"] = questions
    if drift is not None:
        output["drift"] = drift
    return json.dumps(output, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Query specback-generated JSON artifacts (source-map, trace, inventory, questions, drift).",
    )
    parser.add_argument(
        "query", nargs="?", default="",
        help="Substring search on source unit name or path",
    )
    parser.add_argument(
        "--specback-dir", default=".specback",
        help="Path to .specback/ directory (default: .specback)",
    )
    parser.add_argument(
        "--uncovered", action="store_true",
        help="List source units not covered by any spec chapter",
    )
    parser.add_argument(
        "--confidence", choices=["🟢", "🟡", "🔴"], default=None,
        help="Filter by confidence level (requires spec chapter files)",
    )
    parser.add_argument(
        "--questions", nargs="?", const="open", choices=["open", "all"],
        help="Show questions (default: open)",
    )
    parser.add_argument(
        "--chapter", type=str, default=None,
        help="Filter by chapter slug (e.g. 03-data-model)",
    )
    parser.add_argument(
        "--role", type=str, default=None,
        help="Filter by source unit role (e.g. orm_model, endpoint, module)",
    )
    parser.add_argument(
        "--drift", action="store_true",
        help="Show drift report summary",
    )
    parser.add_argument(
        "--format", choices=["text", "json"], default="text",
        help="Output format (default: text)",
    )
    return parser.parse_args(argv)


def _filter_by_confidence(
    results: list[SourceUnitResult],
    target: str,
) -> list[SourceUnitResult]:
    """Filter results by confidence level."""
    return [r for r in results if r.confidence == target]


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = parse_args(argv)

    specback_dir = Path(args.specback_dir)
    if not specback_dir.is_dir():
        print(
            f"ERROR: {args.specback_dir} is not a directory. "
            "Run specback first or pass --specback-dir.",
            file=sys.stderr,
        )
        return 2

    try:
        index = build_index(specback_dir)
    except SpecbackDataError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    # ── Collect output sections ───────────────────────────────────────

    results: list[SourceUnitResult] = []
    result_title = ""
    questions: list[dict[str, Any]] | None = None
    drift: dict[str, Any] | None = None

    # 1. Name/path search
    if args.query:
        results = search_by_name(index, args.query)
        result_title = f"「{args.query}」"

    # 2. Uncovered filter
    if args.uncovered:
        uncovered = find_uncovered(index)
        if results:
            ids = {r.src_id for r in results}
            uncovered = [u for u in uncovered if u.src_id in ids]
        results = uncovered
        result_title = "Uncovered"

    # 3. Chapter filter
    if args.chapter:
        chapter_results = filter_by_chapter(index, args.chapter)
        if results:
            ids = {r.src_id for r in results}
            chapter_results = [r for r in chapter_results if r.src_id in ids]
        results = chapter_results
        if not result_title:
            result_title = f"📘 {args.chapter}"

    # 4. Role filter
    if args.role:
        role_results = filter_by_role(index, args.role)
        if results:
            ids = {r.src_id for r in results}
            role_results = [r for r in role_results if r.src_id in ids]
        results = role_results
        if not result_title:
            result_title = f"Role: {args.role}"

    # 5. Confidence filter (Phase 2 — requires parsing spec chapter files)
    if args.confidence:
        # Compute confidence lazily — only for results that need it
        for r in results:
            if r.confidence is None:
                r.confidence = _extract_confidence(r.src_id, index.specback_dir)
        results = _filter_by_confidence(results, args.confidence)
        if not result_title:
            result_title = f"confidence {args.confidence}"

    # 6. Questions
    if args.questions is not None:
        questions = get_questions(index, args.questions)

    # 7. Drift
    if args.drift:
        drift = get_drift_summary(index)

    # ── Output ────────────────────────────────────────────────────────

    if args.format == "json":
        print(format_results_json(results, questions, drift))
        return 0

    output_parts: list[str] = []

    if drift is not None:
        output_parts.append(format_drift_text(drift))
    elif args.drift:
        output_parts.append("🔄 Drift Report: (none — run detect-drift.py first)")

    if questions is not None:
        output_parts.append(format_questions_text(questions, args.questions or "open"))

    if results or result_title:
        output_parts.append(format_text_results(results, result_title))

    if output_parts:
        print("\n\n".join(output_parts))
    else:
        print("🔍 No results match the given criteria.")
        print("  Hint: specify --query, --uncovered, --chapter, --role, --questions, or --drift.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
