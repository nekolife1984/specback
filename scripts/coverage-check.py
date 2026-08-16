#!/usr/bin/env python3
"""
specback coverage-check.py

Verification script for Phase 4 (Verify). Checks not only inventory
mentions but also per-chapter quality metrics (REF count, body line
count, code-block count, Mermaid count, etc.),
Question Bank integrity, MECE coverage, and outline-mode entity
enumeration in a single pass.

Checks performed:

1.  `<!-- REF: ... -->` count per chapter (`--min-refs-per-chapter`)
2.  Body-line count per chapter (`--min-lines-per-chapter`, default 0 — tone-guided)
3.  Fenced-code-block count per chapter (`--min-code-blocks-per-chapter`)
4.  Mermaid-diagram count per chapter (`--min-mermaid-per-chapter`)
5.  Auto-derived minimum inventory size = max(50, file_count // 20)  (`--min-inventory`)
6.  Upper bound on the ratio of grouping-style INVs like controller_group (`--max-macro-ratio`)
7.  Total `questions.json` count (`--min-questions`)
8.  Upper bound on the `status: open` ratio after Phase 5 (`--max-open-ratio`)
9.  inventory.covered_by fill rate (`--min-covered-by-fill`)
10. MECE check (consults `.specback/trace.json`, `--min-mece-coverage`)
11. **User-custom deliverables**: every filename in
    `goal.json.user_custom_deliverables` must exist in the target directory
    AND have a non-empty body (>= 10 non-blank lines outside code fences).
    These files are exempt from checks 1-4 (the per-chapter comprehensive
    quality gates) because their quality bar is the user's intent expressed
    in `free_text_notes`, not the source-code-spec-chapter gates. Only
    existence + body presence is enforced.
13. **Reserved file body lines** (`--require-min-body-lines-for-reserved`):
    `00-metadata.md`, `99-unresolved.md`, and `traceability.md` must have
    at least N body lines (default: 5). Prevents these files from being
    delivered empty.
14. **Mermaid styling prohibition** (`--forbid-mermaid-styling`):
    Scans Mermaid code blocks for `style A fill:#...`, `classDef ... fill:#...`,
    `stroke:#...`, `color:#...`. Default ON. Violations are reported per file.
15. **Placeholder detection** (`--forbid-placeholder-pattern`):
    Scans for remaining placeholder text: `Phase [0-9]+ で記入予定`, `TODO`,
    `FIXME`. Additional patterns can be specified via CLI.

`--fail-on-uncovered` `--strict` `--output-format` remain for backward
compatibility. Every quality check returns exit 1 on failure. All thresholds
are overridable via CLI flags.

Usage:
    python coverage-check.py \\
      --specback-dir .specback \\
      --target-dir-for-required final \\
      --min-refs-per-chapter 10 \\
      --min-code-blocks-per-chapter 3 \\
      --min-mermaid-per-chapter 1 \\
      --min-inventory auto \\
      --max-macro-ratio 0.2 \\
      --min-questions 10 \\
      --max-open-ratio 0.2 \\
      --min-covered-by-fill 0.9 \\
      --min-mece-coverage 0.7 \\
      --code-block-line-weight 0.5

Exit codes:
    0 = all checks PASS
    1 = one or more checks FAILED
    2 = required file (e.g. inventory.json) could not be loaded
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import artifact_io
from common import reject_nonfinite
from refutils import count_refs


# ----------------------------------------------------------------------------
# Chapter-file naming convention
# ----------------------------------------------------------------------------

NAMING_PATTERN = re.compile(r"^(0\d|[1-9]\d)-[a-z0-9-]+\.md$")
USER_CUSTOM_NAMING_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*\.md$")
NAMING_EXEMPT = {"traceability.md", "README.md"}
REQUIRED_FILES = ("00-metadata.md", "99-unresolved.md", "traceability.md")

# Regexes used in chapter bodies (REF markers live in scripts/refutils.py)
CODE_FENCE_RE = re.compile(r"^```([a-zA-Z0-9_-]+)?")
MERMAID_FENCE_RE = re.compile(r"^```mermaid\b")

# Keywords that make an INV count as "macro" (matched against the `type` field)
MACRO_TYPE_KEYWORDS = ("group", "module", "domain", "category", "bundle", "section")


# ----------------------------------------------------------------------------
# Data classes
# ----------------------------------------------------------------------------

@dataclass
class InventoryItem:
    id: str
    type: str
    name: str
    file: str
    line: int | None
    covered_by: list[str] = field(default_factory=list)
    related_source_ids: list[str] = field(default_factory=list)


@dataclass
class ChapterMetrics:
    file: str
    total_lines: int
    body_lines: int
    refs: int
    code_blocks: int
    mermaid_blocks: int
    code_block_lines: int = 0  # non-blank lines inside fenced code blocks (for weighted adjustment)
    failures: list[str] = field(default_factory=list)


@dataclass
class CoverageReport:
    # backward compatibility
    total_inventory: int = 0
    covered: int = 0
    uncovered: list[InventoryItem] = field(default_factory=list)
    coverage_rate: float = 0.0
    drafts_scanned: int = 0
    questions_total: int = 0
    questions_open: int = 0
    questions_blocked_referenced: list[str] = field(default_factory=list)
    integrity_issues: list[str] = field(default_factory=list)
    naming_warnings: list[str] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)
    target_dir_for_required: str = ""
    # extended fields
    chapter_metrics: list[ChapterMetrics] = field(default_factory=list)
    inventory_required_min: int = 0
    macro_inventory_ratio: float = 0.0
    macro_inventory_count: int = 0
    covered_by_fill_rate: float = 0.0
    open_question_ratio: float = 0.0
    mece_total: int = 0
    mece_covered: int = 0
    mece_excluded: int = 0
    mece_uncovered: int = 0
    mece_passed_strict: bool = True
    mece_coverage_rate: float = 0.0
    gate_failures: list[str] = field(default_factory=list)
    # outline / interactive support
    depth_mode: str = "comprehensive"
    confidence_verified: int = 0
    confidence_inferred: int = 0
    confidence_assumed: int = 0
    # user-custom deliverables (intent-vs-delivery audit, check 12)
    user_custom_expected: list[str] = field(default_factory=list)
    user_custom_failures: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------------
# Loaders
# ----------------------------------------------------------------------------

def load_inventory(path: Path) -> list[InventoryItem]:
    if not path.exists():
        raise FileNotFoundError(f"inventory.json not found: {path}")
    try:
        data = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=reject_nonfinite,
        )
    except json.JSONDecodeError as e:
        raise ValueError(f"inventory.json is not valid JSON: {path}: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"inventory.json must be a JSON object: {path}")
    items: list[InventoryItem] = []
    for entry in data.get("units", []):
        if not isinstance(entry, dict):
            raise ValueError(f"inventory.json unit entry must be an object: {path}")
        try:
            items.append(
                InventoryItem(
                    id=entry["id"],
                    type=entry.get("type", ""),
                    name=entry["name"],
                    file=entry.get("file", ""),
                    line=entry.get("line"),
                    covered_by=list(entry.get("covered_by", [])),
                    related_source_ids=list(entry.get("related_source_ids", [])),
                )
            )
        except KeyError as e:
            raise ValueError(
                f"inventory.json unit is missing required key {e}: {path}"
            ) from e
    return items


def load_questions(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=reject_nonfinite,
        )
    except (json.JSONDecodeError, ValueError):
        return []
    if isinstance(data, dict):
        return list(data.get("questions", []))
    if isinstance(data, list):
        return data
    return []


def load_source_map_ids(specback_dir: Path) -> set[str]:
    """Return the set of unit IDs from source-map.json, or empty set if absent."""
    sm = specback_dir / "source-map.json"
    if not sm.exists():
        return set()
    try:
        data = json.loads(
            sm.read_text(encoding="utf-8"),
            parse_constant=reject_nonfinite,
        )
        return {u["id"] for u in data.get("units", []) if "id" in u}
    except (json.JSONDecodeError, OSError, KeyError, ValueError):
        return set()


def load_source_map_count(specback_dir: Path) -> int | None:
    """Return the total file count from source-map.json if available (used by min-inventory auto)."""
    sm = specback_dir / "source-map.json"
    if not sm.exists():
        return None
    try:
        data = json.loads(
            sm.read_text(encoding="utf-8"),
            parse_constant=reject_nonfinite,
        )
        return int(data.get("stats", {}).get("files_scanned", 0))
    except Exception:
        return None


def load_trace(specback_dir: Path) -> dict[str, Any] | None:
    """Return trace.json content, or None when the file is missing.

    Delegates to :func:`artifact_io.load_trace` (Issue #283); the
    missing-file policy (None) is unchanged.
    """
    return artifact_io.load_trace(specback_dir / "trace.json")


def load_goal_json(specback_dir: Path) -> dict[str, Any] | None:
    """Return the parsed goal.json dict, or None when missing/unreadable.

    Depth-mode detection, template-threshold resolution, and the
    user-custom deliverable loader all read the same file; a single loader
    keeps them in sync (Issue #284 dedup). Invalid JSON, non-dict JSON, and
    missing files all yield None.
    """
    goal_path = specback_dir / "goal.json"
    if not goal_path.exists():
        return None
    try:
        with goal_path.open(encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def load_user_custom_deliverables(specback_dir: Path) -> list[str]:
    """Read `goal.json.user_custom_deliverables` if present; return [] otherwise.

    These are extra filenames the user explicitly requested in `free_text_notes`
    during Phase 0. They are exempt from the standard chapter-naming regex and
    must exist in the target directory at Phase 6 (intent-vs-delivery audit).
    Per-chapter comprehensive quality gates (200 lines / 10 REFs / Mermaid)
    are NOT applied to these files; only existence + non-empty
    body (check 12) is enforced.
    """
    data = load_goal_json(specback_dir) or {}
    raw = data.get("user_custom_deliverables", [])
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, str) and USER_CUSTOM_NAMING_PATTERN.match(item):
            out.append(item)
    return out


def scan_chapter_files(target_dir: Path) -> dict[str, str]:
    """Return a `name → content` map of the .md files directly under the target directory."""
    if not target_dir.exists() or not target_dir.is_dir():
        return {}
    out: dict[str, str] = {}
    for md in sorted(target_dir.glob("*.md")):
        try:
            out[md.name] = md.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            out[md.name] = md.read_text(encoding="utf-8", errors="replace")
    return out


# ----------------------------------------------------------------------------
# Chapter-metric computation
# ----------------------------------------------------------------------------

def iter_fence_state(content: str) -> Iterator[tuple[str, bool, bool]]:
    """Yield ``(line, in_code, is_fence)`` for every line of ``content``.

    ``in_code`` is True when the line lies inside a fenced code block (a
    fence was opened on an earlier line and not yet closed). Fence marker
    lines are yielded with the state that precedes them, so an opening
    fence has ``in_code is False`` / ``is_fence is True`` — callers can
    detect openings with ``is_fence and not in_code``.

    This single helper replaces the four copy-pasted fence-tracking loops
    that used to live in the metric / deliverable / reserved-file /
    placeholder checks (Issue #284 dedup).
    """
    in_code = False
    for line in content.splitlines():
        is_fence = CODE_FENCE_RE.match(line) is not None
        yield line, in_code, is_fence
        if is_fence:
            in_code = not in_code


def compute_chapter_metrics(name: str, content: str) -> ChapterMetrics:
    raw_lines = content.splitlines()
    total = len(raw_lines)

    # Body lines = lines excluding blanks, code fences, and auto-generated comments.
    # code_block_lines = non-blank lines inside fenced code blocks (weighted later).
    body_lines = 0
    code_block_lines = 0
    code_blocks = 0
    mermaid_blocks = 0
    refs = 0

    for line, in_code, is_fence in iter_fence_state(content):
        if is_fence:
            if not in_code:  # opening fence
                if MERMAID_FENCE_RE.match(line):
                    mermaid_blocks += 1
                else:
                    code_blocks += 1
            continue
        if in_code:
            stripped = line.strip()
            if stripped:
                code_block_lines += 1
            continue
        stripped = line.strip()
        if not stripped:
            continue
        body_lines += 1
        refs += count_refs(line)

    return ChapterMetrics(
        file=name,
        total_lines=total,
        body_lines=body_lines,
        refs=refs,
        code_blocks=code_blocks,
        mermaid_blocks=mermaid_blocks,
        code_block_lines=code_block_lines,
    )


def evaluate_chapter_gates(
    metrics: list[ChapterMetrics],
    *,
    min_refs: int,
    min_lines: int,
    min_code_blocks: int,
    min_mermaid: int,
    code_block_line_weight: float = 0.5,
) -> None:
    """Populate `failures` on each ChapterMetrics (per-chapter threshold violations).

    ``code_block_line_weight`` controls what fraction of non-blank code-block
    lines is added to the effective body-line count.  Default 0.5 means that
    every two lines inside a code fence count as one body line toward the
    ``min_lines`` threshold.
    """
    skipped_files = {"00-metadata.md", "99-unresolved.md", "traceability.md", "README.md"}
    for m in metrics:
        if m.file in skipped_files:
            continue
        effective_lines = m.body_lines + int(m.code_block_lines * code_block_line_weight)
        if m.refs < min_refs:
            m.failures.append(f"<!-- REF: ... --> count is {m.refs} < required {min_refs}")
        if effective_lines < min_lines:
            m.failures.append(
                f"body lines {m.body_lines} (code-block-adjusted: {effective_lines}) < required {min_lines}"
            )
        if m.code_blocks < min_code_blocks:
            m.failures.append(f"code blocks {m.code_blocks} < required {min_code_blocks}")
        if m.mermaid_blocks < min_mermaid:
            m.failures.append(f"Mermaid diagrams {m.mermaid_blocks} < required {min_mermaid}")


# ----------------------------------------------------------------------------
# Existing logic (macro ratio, naming, INV mention check, etc.)
# ----------------------------------------------------------------------------

def detect_mentions(item: InventoryItem, drafts: dict[str, str]) -> list[str]:
    mentioned_in: list[str] = []
    name_pattern = re.compile(rf"\b{re.escape(item.name)}\b") if item.name else None
    for draft_name, content in drafts.items():
        if item.id and item.id in content:
            mentioned_in.append(draft_name)
            continue
        if name_pattern and name_pattern.search(content):
            mentioned_in.append(draft_name)
            continue
        if item.file and item.file in content:
            mentioned_in.append(draft_name)
            continue
    return mentioned_in


def is_macro_type(item: InventoryItem) -> bool:
    return any(k in item.type.lower() for k in MACRO_TYPE_KEYWORDS)


def check_question_integrity(
    questions: list[dict[str, Any]],
    inventory_ids: set[str],
    drafts: dict[str, str],
) -> tuple[list[str], list[str]]:
    issues: list[str] = []
    required_fields = {"id", "category", "body", "severity", "status"}
    valid_severities = {"critical", "important", "nice-to-have"}
    valid_statuses = {"open", "asked", "answered", "abandoned", "skipped"}

    question_ids: set[str] = set()
    for q in questions:
        qid = q.get("id", "<no-id>")
        question_ids.add(qid)
        missing = required_fields - set(q.keys())
        if missing:
            issues.append(f"{qid}: missing required fields: {sorted(missing)}")
        sev = q.get("severity")
        if sev and sev not in valid_severities:
            issues.append(f"{qid}: invalid severity value: {sev}")
        st = q.get("status")
        if st and st not in valid_statuses:
            issues.append(f"{qid}: invalid status value: {st}")
        if st == "answered":
            if not q.get("answer"):
                issues.append(f"{qid}: status=answered but `answer` is empty")
        related_inv = q.get("related_inventory_ids", []) or []
        for inv_id in related_inv:
            if inv_id not in inventory_ids:
                issues.append(
                    f"{qid}: related_inventory_ids entry {inv_id} not found in inventory.json"
                )

    blocked_pattern = re.compile(r"<!--\s*BLOCKED:\s*see\s+(Q-[A-Za-z0-9_-]+)\s*-->")
    blocked_referenced: list[str] = []
    for content in drafts.values():
        for match in blocked_pattern.finditer(content):
            ref_id = match.group(1)
            if ref_id not in question_ids:
                issues.append(f"draft contains <!-- BLOCKED: see {ref_id} --> but the question is missing from questions.json")
            else:
                blocked_referenced.append(ref_id)

    return issues, sorted(set(blocked_referenced))


def check_naming_convention(drafts_dir: Path, user_custom: list[str] | None = None) -> list[str]:
    """Flag files that violate the chapter-naming regex.

    `user_custom` extends `NAMING_EXEMPT` dynamically; entries listed in
    `goal.json.user_custom_deliverables` are NOT counted as naming violations.
    """
    if not drafts_dir.exists() or not drafts_dir.is_dir():
        return []
    allowed_exempt = set(NAMING_EXEMPT) | set(user_custom or [])
    warnings: list[str] = []
    for f in sorted(drafts_dir.glob("*.md")):
        if f.name in allowed_exempt:
            continue
        if not NAMING_PATTERN.match(f.name):
            warnings.append(
                f"{f.name} violates the naming convention ({NAMING_PATTERN.pattern}) and is not in the reserved list {sorted(NAMING_EXEMPT)} or the user_custom_deliverables list"
            )
    return warnings


def check_user_custom_deliverables(
    target_dir: Path,
    user_custom: list[str],
    min_body_lines: int = 10,
    code_block_line_weight: float = 0.5,
) -> list[str]:
    """Verify every user-custom deliverable exists in the target dir with a non-empty body.

    "Non-empty body" means at least `min_body_lines` effective non-blank lines outside
    of code fences, where code-block lines are weighted by ``code_block_line_weight``.
    This catches the case where the agent stubs the file but never fills it.

    Per-chapter comprehensive quality gates (200 lines, REFs, Mermaid, etc.)
    do NOT apply to user_custom files — those are handled by the caller via
    explicit exclusion from `evaluate_chapter_gates`. Only existence + body
    presence is enforced here (check 12).
    """
    failures: list[str] = []
    if not user_custom:
        return failures
    if not target_dir.exists():
        return [f"target directory {target_dir} does not exist (cannot verify user-custom deliverables)"]
    for name in user_custom:
        p = target_dir / name
        if not p.exists():
            failures.append(f"user-custom deliverable {name} is missing from {target_dir}")
            continue
        try:
            content = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = p.read_text(encoding="utf-8", errors="replace")
        body_lines = 0
        code_block_lines = 0
        for line, in_code, is_fence in iter_fence_state(content):
            if is_fence:
                continue
            if in_code:
                stripped = line.strip()
                if stripped:
                    code_block_lines += 1
                continue
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("<!--") or stripped.startswith("-->"):
                continue
            body_lines += 1
        effective_lines = body_lines + int(code_block_lines * code_block_line_weight)
        if effective_lines < min_body_lines:
            failures.append(
                f"user-custom deliverable {name} exists but body has only {body_lines} "
                f"effective lines (code-block-adjusted: {effective_lines}) "
                f"(need >= {min_body_lines})"
            )
    return failures


def check_required_files(target_dir: Path) -> list[str]:
    missing: list[str] = []
    if not target_dir.exists():
        return [f"target directory {target_dir} does not exist"]
    for required in REQUIRED_FILES:
        if not (target_dir / required).exists():
            missing.append(f"required file {required} is missing from {target_dir}")
    return missing


# ----------------------------------------------------------------------------
# Report construction
# ----------------------------------------------------------------------------

def detect_depth_mode(specback_dir: Path) -> str:
    """Read goal.json (if present) and return the configured depth_mode.

    Returns "comprehensive" when the field is missing — that preserves the
    legacy behaviour for projects that pre-date the outline mode flag.
    """
    data = load_goal_json(specback_dir)
    if data is None:
        return "comprehensive"
    mode = data.get("depth_mode")
    if mode not in {"comprehensive", "outline", "interactive"}:
        return "comprehensive"
    return mode


# ---------------------------------------------------------------------------
# Template-aware threshold defaults
# ---------------------------------------------------------------------------

TEMPLATE_THRESHOLDS: dict[str, dict[str, float]] = {
    "library-sdk": {
        "min_covered_by_fill": 0.3,
        "min_mece_coverage": 0.4,
    },
}

_DEFAULT_COVERED_BY_FILL = 0.9
_DEFAULT_MECE_COVERAGE = 0.7


def _detect_template(specback_dir: Path) -> str | None:
    """Read goal.json and return the template name, or None if unknown."""
    data = load_goal_json(specback_dir)
    if data is None:
        return None
    tmpl = data.get("template")
    if not isinstance(tmpl, str) or tmpl not in TEMPLATE_THRESHOLDS:
        return None
    return tmpl


def _resolve_template_threshold(
    specback_dir: Path,
    cli_value: float | None,
    key: str,
    default: float,
) -> float:
    """Return the effective template-aware threshold.

    Honour an explicit CLI value; otherwise consult goal.json's template
    and fall back to ``default`` when the template has no entry for ``key``.
    Single implementation shared by the covered-by-fill and MECE resolvers
    (previously two copy-paste twins, Issue #284 dedup).
    """
    if cli_value is not None:
        return cli_value
    tmpl = _detect_template(specback_dir)
    if tmpl is not None and tmpl in TEMPLATE_THRESHOLDS:
        return TEMPLATE_THRESHOLDS[tmpl][key]
    return default


def _resolve_covered_by_fill(specback_dir: Path, cli_value: float | None) -> float:
    """Return the effective min-covered-by-fill threshold.

    If the user passed an explicit CLI value, honour it.
    Otherwise, consult goal.json for the template and return the
    template-specific default, falling back to 0.9.
    """
    return _resolve_template_threshold(
        specback_dir, cli_value, "min_covered_by_fill", _DEFAULT_COVERED_BY_FILL
    )


def _resolve_mece_coverage(specback_dir: Path, cli_value: float | None) -> float:
    """Return the effective min-mece-coverage threshold (see _resolve_covered_by_fill)."""
    return _resolve_template_threshold(
        specback_dir, cli_value, "min_mece_coverage", _DEFAULT_MECE_COVERAGE
    )


# ----------------------------------------------------------------------------
# New checks for #158: reserved body lines, Mermaid styling, placeholders
# ----------------------------------------------------------------------------

RESERVED_FILES = {"00-metadata.md", "99-unresolved.md", "traceability.md"}

MERMAID_STYLE_RE = re.compile(
    r"(?:style\s+\w+\s+fill:|classDef\s+\w+\s+fill:|stroke:|color:)"
)

DEFAULT_PLACEHOLDER_PATTERNS = [
    re.compile(r"Phase\s+[0-9]+\s+で記入予定"),
    re.compile(r"\bTODO\b"),
    re.compile(r"\bFIXME\b"),
]


def check_reserved_body_lines(
    chapters: dict[str, str],
    min_lines: int,
) -> list[str]:
    """Check that reserved files (00-metadata.md, 99-unresolved.md, traceability.md)
    have at least ``min_lines`` non-blank body lines. Returns failure messages."""
    failures: list[str] = []
    if min_lines <= 0:
        return failures
    for name, content in chapters.items():
        if name not in RESERVED_FILES:
            continue
        # Count non-blank lines outside code fences (same logic as compute_chapter_metrics)
        body_lines = 0
        for line, in_code, is_fence in iter_fence_state(content):
            if is_fence:
                continue
            if in_code:
                continue
            stripped = line.strip()
            if not stripped:
                continue
            body_lines += 1
        if body_lines < min_lines:
            failures.append(
                f"reserved file {name} has only {body_lines} body lines "
                f"(minimum: {min_lines})"
            )
    return failures


def check_mermaid_styling(chapters: dict[str, str]) -> list[str]:
    """Check that Mermaid code blocks contain no style/classDef fill/stroke/color directives.

    Returns a list of failure messages with file name and offending line count.
    The check is skipped when no files are provided (empty dict).
    """
    failures: list[str] = []
    for name, content in chapters.items():
        in_mermaid = False
        offending_lines: list[str] = []
        for line in content.splitlines():
            stripped = line.strip()
            if MERMAID_FENCE_RE.match(stripped):
                in_mermaid = True
                continue
            if CODE_FENCE_RE.match(stripped) and in_mermaid:
                in_mermaid = False
                continue
            if in_mermaid and MERMAID_STYLE_RE.search(stripped):
                offending_lines.append(stripped[:80])
        if offending_lines:
            detail = "; ".join(offending_lines[:5])
            if len(offending_lines) > 5:
                detail += f" ... (+{len(offending_lines) - 5} more)"
            failures.append(
                f"Mermaid styling forbidden in {name}: "
                f"{len(offending_lines)} line(s) with style/color directives — "
                f"use structure-only Mermaid ({detail})"
            )
    return failures


# Edge label inside a Mermaid flowchart edge: |...|
MERMAID_EDGE_LABEL_RE = re.compile(r"\|([^|\n]*)\|")
# Cylinder node opening: [( ... )]  — must close with )]
MERMAID_CYLINDER_OPEN_RE = re.compile(r"\[\s*\(")


def _mermaid_unquoted(block: str) -> str:
    """Return the block with double-quoted strings blanked out.

    Mermaid labels use double quotes for literal text; parentheses inside
    quoted labels are legal (e.g. `A["OpenAIModel (OpenAI互換API)"]`).
    Blanking quoted spans avoids false positives from literal text.
    """
    return re.sub(r'"[^"]*"', '""', block)


def check_mermaid_syntax(chapters: dict[str, str]) -> list[str]:
    """Static syntax sanity checks for Mermaid code blocks.

    Catches the two most common Mermaid parse errors that break rendering
    on GitHub etc. without invoking a full Mermaid parser:

    1. Unquoted parentheses inside an edge label ``|...|``
       (e.g. ``E -->|OpenAIModel (OpenAI互換API)| P``).
    2. Cylinder node ``[( ... )]`` opened but not closed with ``)]``
       (e.g. ``DB[(SQLite)<br/>F-004 永続化]``).

    Quoted strings are ignored so legal labels do not produce false
    positives. Returns a list of failure messages (empty when OK).
    """
    failures: list[str] = []
    for name, content in chapters.items():
        in_mermaid = False
        for line in content.splitlines():
            stripped = line.strip()
            if MERMAID_FENCE_RE.match(stripped):
                in_mermaid = True
                continue
            if CODE_FENCE_RE.match(stripped) and in_mermaid:
                in_mermaid = False
                continue
            if not in_mermaid:
                continue
            # 1) edge labels: |...| must not contain unquoted parentheses
            unquoted_line = _mermaid_unquoted(stripped)
            for m in MERMAID_EDGE_LABEL_RE.finditer(unquoted_line):
                label = m.group(1)
                if "(" in label or ")" in label:
                    failures.append(
                        f"{name}: Mermaid edge label contains unquoted "
                        f"parentheses: {m.group(0)} (quote the label, "
                        f"e.g. |\"text (…)\"|)"
                    )
            # 2) cylinder nodes: [( ... )] must close with )]
            for cm in MERMAID_CYLINDER_OPEN_RE.finditer(unquoted_line):
                rest = unquoted_line[cm.end() :]
                # Look for a matching )] on the same line (no nesting depth
                # tracking — cylinders are flat in practice).
                close_idx = rest.find(")]")
                if close_idx == -1:
                    failures.append(
                        f"{name}: Mermaid cylinder node opens with [( "
                        f"but is not closed with )] on line: {stripped[:80]}"
                    )
    return failures


def check_placeholder_patterns(
    chapters: dict[str, str],
    extra_patterns: list[str] | None = None,
) -> list[str]:
    """Check that chapter files contain no placeholder text.

    Built-in patterns: ``Phase [0-9]+ で記入予定``, ``TODO``, ``FIXME``.
    ``extra_patterns`` adds custom regex patterns.

    Returns a list of failure messages.
    """
    patterns = list(DEFAULT_PLACEHOLDER_PATTERNS)
    if extra_patterns:
        for p in extra_patterns:
            try:
                patterns.append(re.compile(p))
            except re.error:
                patterns.append(re.compile(re.escape(p)))
    failures: list[str] = []
    for name, content in chapters.items():
        for i, (line, in_code, is_fence) in enumerate(
            iter_fence_state(content), start=1
        ):
            stripped = line.strip()
            # Skip code fences (placeholders are expected inside code blocks
            # as examples). Track fence state so fenced body lines are
            # ignored, not just the fence markers themselves (Issue #257).
            if is_fence:
                continue
            if in_code:
                continue
            for pat in patterns:
                if pat.search(stripped):
                    failures.append(
                        f"placeholder in {name}:{i} matches {pat.pattern!r}: {stripped[:80]}"
                    )
                    break  # one violation per line is enough
    return failures


def resolve_target_dir(output_dir: Path, target_dir_name: str) -> Path:
    """Resolve the directory that holds the chapter files.

    1. Try ``output_dir / target_dir_name`` (e.g. ``.specback/final``).
    2. If that does not exist, try ``target_dir_name`` as a standalone path
       (e.g. ``.specback/drafts`` when ``--output-dir`` points elsewhere).
    """
    target_dir = output_dir / target_dir_name
    if not target_dir.exists():
        fallback = Path(target_dir_name)
        if fallback.exists():
            target_dir = fallback
    return target_dir


def compute_mention_coverage(
    inventory: list[InventoryItem], chapters: dict[str, str]
) -> list[InventoryItem]:
    """Fill ``covered_by`` via mention detection and return the uncovered items.

    Items that already carry ``covered_by`` values (set by the agent) are
    left untouched. Mutates ``item.covered_by`` in place — the returned
    report shares the same item objects (legacy behaviour).
    """
    uncovered: list[InventoryItem] = []
    for item in inventory:
        # Use any existing covered_by values (set by the agent if filled manually).
        if not item.covered_by:
            item.covered_by = detect_mentions(item, chapters)
        if not item.covered_by:
            uncovered.append(item)
    return uncovered


def compute_required_min_inventory(min_inventory: str | int, specback_dir: Path) -> int:
    """Resolve the effective minimum inventory size.

    ``"auto"`` derives it from source-map.json's file count via
    ``max(50, files_scanned // 20)``; any other value is used verbatim.
    """
    if min_inventory == "auto":
        file_count = load_source_map_count(specback_dir) or 0
        return max(50, file_count // 20)
    return int(min_inventory)


def compute_macro_stats(inventory: list[InventoryItem]) -> tuple[int, float]:
    """Return ``(macro_count, macro_ratio)`` for the inventory."""
    macro_count = sum(1 for it in inventory if is_macro_type(it))
    macro_ratio = (macro_count / len(inventory)) if inventory else 0.0
    return macro_count, macro_ratio


def compute_covered_by_fill_rate(inventory: list[InventoryItem]) -> float:
    """Return the fraction of inventory items with a non-empty ``covered_by``."""
    filled = sum(1 for it in inventory if it.covered_by)
    return (filled / len(inventory)) if inventory else 0.0


def compute_open_question_stats(questions: list[dict[str, Any]]) -> tuple[int, float]:
    """Return ``(open_count, open_ratio)`` for the question bank."""
    open_q = sum(1 for q in questions if q.get("status") == "open")
    open_ratio = (open_q / len(questions)) if questions else 0.0
    return open_q, open_ratio


@dataclass
class MeceStats:
    """MECE statistics derived from trace.json (all-zero when absent)."""

    total: int = 0
    covered: int = 0
    excluded: int = 0
    uncovered: int = 0
    passed_strict: bool = True
    coverage_rate: float = 0.0


def compute_mece_stats(trace: dict[str, Any] | None) -> MeceStats | None:
    """Derive MECE stats from trace.json; None when the trace is missing."""
    if trace is None:
        return None
    total = trace.get("source_units_total", 0)
    covered = trace.get("source_units_covered", 0)
    excluded = trace.get("source_units_excluded", 0)
    uncovered = trace.get("source_units_uncovered", 0)
    denom = max(total - excluded, 1)
    return MeceStats(
        total=total,
        covered=covered,
        excluded=excluded,
        uncovered=uncovered,
        passed_strict=uncovered == 0,
        coverage_rate=covered / denom,
    )


def evaluate_gates(
    *,
    inventory_count: int,
    required_min: int,
    macro_ratio: float,
    max_macro_ratio: float,
    macro_count: int,
    covered_by_fill_rate: float,
    min_covered_by_fill: float,
    questions_count: int,
    min_questions: int,
    open_ratio: float,
    max_open_ratio: float,
    mece: MeceStats | None,
    min_mece_coverage: float,
) -> list[str]:
    """Return the aggregate gate-failure messages for the fixed thresholds."""
    gate_failures: list[str] = []
    if inventory_count < required_min:
        gate_failures.append(
            f"inventory.json size {inventory_count} < required {required_min} "
            f"(may be under-granular for the codebase size)"
        )
    if macro_ratio > max_macro_ratio:
        gate_failures.append(
            f"macro-type INV ratio {macro_ratio:.1%} > cap {max_macro_ratio:.1%} "
            f"({macro_count}/{inventory_count} are group/module-style — please subdivide)"
        )
    if covered_by_fill_rate < min_covered_by_fill:
        gate_failures.append(
            f"inventory.covered_by fill rate {covered_by_fill_rate:.1%} < {min_covered_by_fill:.1%}"
        )
    if questions_count < min_questions:
        gate_failures.append(
            f"questions.json size {questions_count} < required {min_questions} "
            f"(raise more questions for Phase 5 dialogue)"
        )
    if questions_count and open_ratio > max_open_ratio:
        gate_failures.append(
            f"open-status ratio {open_ratio:.1%} > cap {max_open_ratio:.1%} "
            f"(complete the Phase 5 three-stage dialogue)"
        )
    if mece is None:
        gate_failures.append(
            "trace.json missing. Run build-trace.py to enable the MECE check."
        )
    elif mece.coverage_rate < min_mece_coverage:
        gate_failures.append(
            f"MECE coverage {mece.coverage_rate:.1%} < {min_mece_coverage:.1%} "
            f"(uncovered={mece.uncovered}/{mece.total - mece.excluded})"
        )
    return gate_failures


def check_source_map_refs(
    inventory: list[InventoryItem], source_map_ids: set[str]
) -> list[str]:
    """Return failures for ``related_source_ids`` entries missing from source-map.json."""
    failures: list[str] = []
    for item in inventory:
        for ref in item.related_source_ids:
            if ref not in source_map_ids:
                failures.append(
                    f"{item.id}.related_source_ids contains {ref!r} which is not in source-map.json"
                )
    return failures


def count_confidence_labels(chapters: dict[str, str]) -> tuple[int, int, int]:
    """Count 🟢 VERIFIED / 🟡 INFERRED / 🔴 ASSUMED labels across chapter bodies.

    Word-boundary regexes avoid false positives from negated words such as
    UNVERIFIED / UNASSUMED / DISINFERRED (same pattern as specback-health.py).
    """
    word_verified = re.compile(r"\bVERIFIED\b")
    word_inferred = re.compile(r"\bINFERRED\b")
    word_assumed = re.compile(r"\bASSUMED\b")
    verified = inferred = assumed = 0
    for _, content in chapters.items():
        verified += content.count("🟢") + len(word_verified.findall(content))
        inferred += content.count("🟡") + len(word_inferred.findall(content))
        assumed += content.count("🔴") + len(word_assumed.findall(content))
    return verified, inferred, assumed


def build_report(
    specback_dir: Path,
    *,
    output_dir: Path,
    target_dir_name: str,
    min_inventory: str | int,
    max_macro_ratio: float,
    min_questions: int,
    max_open_ratio: float,
    min_covered_by_fill: float,
    min_refs_per_chapter: int,
    min_lines_per_chapter: int,
    min_code_blocks_per_chapter: int,
    min_mermaid_per_chapter: int,
    min_mece_coverage: float,
    code_block_line_weight: float = 0.5,
    # New checks for #158
    require_min_body_lines_for_reserved: int = 5,
    forbid_mermaid_styling: bool = True,
    forbid_placeholder_pattern: list[str] | None = None,
    check_mermaid_syntax_flag: bool = True,
) -> CoverageReport:
    # Resolve the target directory:
    # 1. Try output_dir / target_dir_name (e.g. .specback/final or specs/final)
    # 2. If that doesn't exist, try target_dir_name as a standalone path
    #    (e.g. .specback/drafts when --output-dir points elsewhere)
    target_dir = resolve_target_dir(output_dir, target_dir_name)

    inventory_path = specback_dir / "inventory.json"
    questions_path = specback_dir / "questions.json"

    inventory = load_inventory(inventory_path)
    questions = load_questions(questions_path)
    chapters = scan_chapter_files(target_dir)
    inventory_ids = {item.id for item in inventory}

    depth_mode = detect_depth_mode(specback_dir)

    # backward compatibility: mention detection
    uncovered = compute_mention_coverage(inventory, chapters)

    integrity_issues, blocked_referenced = check_question_integrity(
        questions, inventory_ids, chapters
    )

    user_custom = load_user_custom_deliverables(specback_dir)
    naming_warnings = check_naming_convention(target_dir, user_custom=user_custom)
    missing_required = check_required_files(target_dir)
    user_custom_failures = check_user_custom_deliverables(
        target_dir, user_custom, code_block_line_weight=code_block_line_weight,
    )

    # Chapter metrics
    chapter_metrics: list[ChapterMetrics] = [
        compute_chapter_metrics(name, content) for name, content in chapters.items()
    ]

    # user_custom chapters are evaluated only by check 12 (existence + non-empty body).
    # The comprehensive per-chapter gates (200 lines / 10 REFs / code blocks / Mermaid)
    # are designed for source-derived spec chapters, not for user-narrated
    # files like manual.md. Split chapter_metrics into "standard" and "user_custom" so
    # only the standard ones receive evaluate_chapter_gates.
    user_custom_set = set(user_custom)
    standard_chapter_metrics = [m for m in chapter_metrics if m.file not in user_custom_set]

    # In outline / interactive mode the comprehensive-mode chapter gates
    # (200 lines / 10 REFs / code blocks / Mermaid) are
    # dropped. Instead, the MECE criterion is "every entity appears in
    # some row of some table" — reuse the uncovered logic below.
    if depth_mode == "comprehensive":
        evaluate_chapter_gates(
            standard_chapter_metrics,
            min_refs=min_refs_per_chapter,
            min_lines=min_lines_per_chapter,
            min_code_blocks=min_code_blocks_per_chapter,
            min_mermaid=min_mermaid_per_chapter,
            code_block_line_weight=code_block_line_weight,
        )

    # inventory min auto
    required_min = compute_required_min_inventory(min_inventory, specback_dir)

    # Macro ratio
    macro_count, macro_ratio = compute_macro_stats(inventory)

    # covered_by fill rate
    covered_by_fill_rate = compute_covered_by_fill_rate(inventory)

    # questions ratio
    open_q, open_ratio = compute_open_question_stats(questions)

    # MECE
    trace = load_trace(specback_dir)
    mece = compute_mece_stats(trace)

    # Gate evaluation
    gate_failures = evaluate_gates(
        inventory_count=len(inventory),
        required_min=required_min,
        macro_ratio=macro_ratio,
        max_macro_ratio=max_macro_ratio,
        macro_count=macro_count,
        covered_by_fill_rate=covered_by_fill_rate,
        min_covered_by_fill=min_covered_by_fill,
        questions_count=len(questions),
        min_questions=min_questions,
        open_ratio=open_ratio,
        max_open_ratio=max_open_ratio,
        mece=mece,
        min_mece_coverage=min_mece_coverage,
    )

    # #158: reserved file body-line check
    gate_failures.extend(
        check_reserved_body_lines(chapters, require_min_body_lines_for_reserved)
    )
    # #158: Mermaid styling check
    if forbid_mermaid_styling:
        gate_failures.extend(check_mermaid_styling(chapters))
    # Mermaid static syntax check (unquoted edge-label parens, cylinder closure)
    if check_mermaid_syntax_flag:
        gate_failures.extend(check_mermaid_syntax(chapters))
    # #158: placeholder pattern check
    gate_failures.extend(check_placeholder_patterns(chapters, forbid_placeholder_pattern))

    # Reflect per-chapter metric failures into the overall gate failures.
    # (user_custom chapters were excluded from evaluate_chapter_gates above; their
    # m.failures is empty even if 200-line / 10-REF gates would have failed.)
    for m in chapter_metrics:
        for f in m.failures:
            gate_failures.append(f"chapter {m.file}: {f}")

    # Reflect user-custom deliverable failures (Phase 6 intent-vs-delivery gate, check 12).
    for f in user_custom_failures:
        gate_failures.append(f"user_custom: {f}")

    # Source-map ↔ inventory cross-reference consistency (check 13).
    # Every `related_source_ids[*]` in inventory must match an `id` in source-map.json.
    # Skipped when source-map.json is absent (early pipeline runs) or inventory is empty.
    source_map_ids = load_source_map_ids(specback_dir)
    if source_map_ids and inventory:
        for ref in check_source_map_refs(inventory, source_map_ids):
            gate_failures.append(f"source-map/inventory: {ref}")

    # Aggregate confidence labels for outline / interactive mode.
    # Count how many times 🟢 VERIFIED / 🟡 INFERRED / 🔴 ASSUMED appear in chapter bodies.
    verified = inferred = assumed = 0
    if depth_mode != "comprehensive":
        verified, inferred, assumed = count_confidence_labels(chapters)
        # Warn if the ASSUMED ratio is too high.
        total_labels = verified + inferred + assumed
        if total_labels > 0:
            assumed_ratio = assumed / total_labels
            if assumed_ratio > 0.6:
                gate_failures.append(
                    f"[outline] ASSUMED ratio is {assumed_ratio:.0%} "
                    f"(over 60%) — strengthen grounding via mechanical extraction"
                )

    total = len(inventory)
    rate = (total - len(uncovered)) / total * 100 if total else 0.0

    return CoverageReport(
        total_inventory=total,
        covered=total - len(uncovered),
        uncovered=uncovered,
        coverage_rate=rate,
        drafts_scanned=len(chapters),
        questions_total=len(questions),
        questions_open=open_q,
        questions_blocked_referenced=blocked_referenced,
        integrity_issues=integrity_issues,
        naming_warnings=naming_warnings,
        missing_required=missing_required,
        target_dir_for_required=str(target_dir),
        chapter_metrics=chapter_metrics,
        inventory_required_min=required_min,
        macro_inventory_ratio=macro_ratio,
        macro_inventory_count=macro_count,
        covered_by_fill_rate=covered_by_fill_rate,
        open_question_ratio=open_ratio,
        mece_total=mece.total if mece else 0,
        mece_covered=mece.covered if mece else 0,
        mece_excluded=mece.excluded if mece else 0,
        mece_uncovered=mece.uncovered if mece else 0,
        mece_passed_strict=mece.passed_strict if mece else True,
        mece_coverage_rate=mece.coverage_rate if mece else 0.0,
        gate_failures=gate_failures,
        depth_mode=depth_mode,
        confidence_verified=verified,
        confidence_inferred=inferred,
        confidence_assumed=assumed,
        user_custom_expected=user_custom,
        user_custom_failures=user_custom_failures,
    )


# ----------------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------------

def render_text(report: CoverageReport) -> str:
    lines: list[str] = []
    lines.append("=== specback Phase 4 verification report (v2) ===")
    lines.append("")
    lines.append(f"[Depth mode] {report.depth_mode}")
    if report.depth_mode != "comprehensive":
        total_labels = (
            report.confidence_verified
            + report.confidence_inferred
            + report.confidence_assumed
        )
        if total_labels > 0:
            v_pct = report.confidence_verified / total_labels * 100
            i_pct = report.confidence_inferred / total_labels * 100
            a_pct = report.confidence_assumed / total_labels * 100
            lines.append(
                f"  Confidence KPI: 🟢 VERIFIED {report.confidence_verified} ({v_pct:.0f}%) / "
                f"🟡 INFERRED {report.confidence_inferred} ({i_pct:.0f}%) / "
                f"🔴 ASSUMED {report.confidence_assumed} ({a_pct:.0f}%)"
            )
        else:
            lines.append("  Confidence KPI: no labels detected — attach 🟢/🟡/🔴 to each table cell")
    lines.append("")
    lines.append("[Inventory coverage]")
    lines.append(f"- Total inventory items: {report.total_inventory} (required minimum: {report.inventory_required_min})")
    lines.append(f"- Mentioned: {report.covered} ({report.coverage_rate:.1f}%)")
    lines.append(f"- Unmentioned: {len(report.uncovered)}")
    lines.append(f"- Macro type: {report.macro_inventory_count} ({report.macro_inventory_ratio:.1%})")
    lines.append(f"- covered_by fill rate: {report.covered_by_fill_rate:.1%}")
    lines.append("")
    lines.append("[MECE check]")
    if report.mece_total > 0:
        lines.append(f"- Total source units: {report.mece_total}")
        lines.append(f"- Covered by the spec: {report.mece_covered} ({report.mece_coverage_rate:.1%})")
        lines.append(f"- Explicitly excluded: {report.mece_excluded}")
        lines.append(f"- Uncovered: {report.mece_uncovered}")
    else:
        lines.append("- trace.json missing; MECE check not performed")
    lines.append("")
    lines.append("[Question Bank]")
    lines.append(f"- Total: {report.questions_total}")
    lines.append(f"- Open remaining: {report.questions_open} ({report.open_question_ratio:.1%})")
    lines.append("")
    lines.append("[Per-chapter quality metrics]")
    for m in report.chapter_metrics:
        flag = "❌" if m.failures else "✅"
        code_extra = ""
        if m.code_block_lines:
            effective = m.body_lines + int(m.code_block_lines * 0.5)
            if effective != m.body_lines:
                code_extra = f" (code-block-adjusted: {effective})"
        lines.append(
            f"  {flag} {m.file}: body={m.body_lines}{code_extra}, refs={m.refs} "
            f"code={m.code_blocks} mermaid={m.mermaid_blocks}"
        )
        for f in m.failures:
            lines.append(f"      - {f}")
    if report.user_custom_expected:
        lines.append("")
        lines.append(f"[User-custom deliverables expected from goal.json: {len(report.user_custom_expected)}]")
        for name in report.user_custom_expected:
            flag = "✅" if not any(name in f for f in report.user_custom_failures) else "❌"
            lines.append(f"  {flag} {name}")
    lines.append("")
    lines.append("[Gate decision]")
    if not report.gate_failures and not report.missing_required:
        lines.append("- ✅ ALL PASSED")
    else:
        for f in report.missing_required:
            lines.append(f"- ❌ {f}")
        for f in report.gate_failures:
            lines.append(f"- ❌ {f}")
    return "\n".join(lines)


def render_json(report: CoverageReport) -> str:
    return json.dumps({
        "total_inventory": report.total_inventory,
        "inventory_required_min": report.inventory_required_min,
        "macro_inventory_count": report.macro_inventory_count,
        "macro_inventory_ratio": report.macro_inventory_ratio,
        "covered_by_fill_rate": report.covered_by_fill_rate,
        "coverage_rate": report.coverage_rate,
        "drafts_scanned": report.drafts_scanned,
        "uncovered_inventory": [
            {"id": x.id, "type": x.type, "name": x.name, "file": x.file, "line": x.line}
            for x in report.uncovered
        ],
        "questions_total": report.questions_total,
        "questions_open": report.questions_open,
        "open_question_ratio": report.open_question_ratio,
        "integrity_issues": report.integrity_issues,
        "naming_warnings": report.naming_warnings,
        "missing_required": report.missing_required,
        "chapter_metrics": [
            {
                "file": m.file,
                "total_lines": m.total_lines,
                "body_lines": m.body_lines,
                "code_block_lines": m.code_block_lines,
                "refs": m.refs,
                "code_blocks": m.code_blocks,
                "mermaid_blocks": m.mermaid_blocks,
                "failures": m.failures,
            }
            for m in report.chapter_metrics
        ],
        "mece_total": report.mece_total,
        "mece_covered": report.mece_covered,
        "mece_excluded": report.mece_excluded,
        "mece_uncovered": report.mece_uncovered,
        "mece_coverage_rate": report.mece_coverage_rate,
        "mece_passed_strict": report.mece_passed_strict,
        "user_custom_expected": report.user_custom_expected,
        "user_custom_failures": report.user_custom_failures,
        "gate_failures": report.gate_failures,
    }, ensure_ascii=False, indent=2)


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Build the CLI parser and return the parsed arguments (Issue #284)."""
    p = argparse.ArgumentParser(description="specback Phase 4 verification (v2)")
    p.add_argument("--specback-dir", type=Path, default=Path.cwd() / ".specback")
    p.add_argument("--target-dir-for-required", default="final",
                   help="Target subdirectory under --output-dir (e.g. 'drafts', 'final'), "
                        "or a standalone path (e.g. '.specback/drafts'). "
                        "If the resolved path does not exist, the value is also tried as a "
                        "standalone path before failing. Default: 'final'.")
    p.add_argument("--output-dir", type=Path, default=None,
                   help="Spec output directory (default: same as --specback-dir)")
    p.add_argument("--output-format", choices=["text", "json"], default="text")

    # Per-chapter thresholds
    p.add_argument("--min-refs-per-chapter", type=int, default=10)
    p.add_argument("--min-lines-per-chapter", type=int, default=0,
                   help="Minimum body lines per chapter (default: 0 — tone-guided; set explicitly to enforce a floor)")
    p.add_argument("--min-code-blocks-per-chapter", type=int, default=3)
    p.add_argument("--min-mermaid-per-chapter", type=int, default=1)
    p.add_argument("--code-block-line-weight", type=float, default=0.5,
                   help='Weight for non-blank code-block lines when counting body lines '
                        '(default: 0.5 — every two code lines count as one body line). '
                        'Set to 0.0 to exclude code-block lines entirely, '
                        'or 1.0 to count them as full body lines.')
    p.add_argument("--require-min-body-lines-for-reserved", type=int, default=5,
                   help="Minimum body lines for reserved files (00-metadata.md, 99-unresolved.md, traceability.md). "
                        "Default: 5. Set to 0 to disable.")
    p.add_argument("--forbid-mermaid-styling", default=True, action=argparse.BooleanOptionalAction,
                   help="Check that Mermaid blocks contain no style/classDef fill/stroke/color directives. "
                        "Default: ON.")
    p.add_argument("--check-mermaid-syntax", dest="check_mermaid_syntax_flag",
                   default=True, action=argparse.BooleanOptionalAction,
                   help="Check Mermaid blocks for common parse errors detectable by static analysis "
                        "(unquoted parentheses in edge labels |...|, cylinder nodes [( ... )] not closed "
                        "with )]). Default: ON.")
    p.add_argument("--forbid-placeholder-pattern", nargs="*", default=None,
                   help="Additional placeholder patterns to forbid (in addition to defaults: "
                        "'Phase [0-9]+ で記入予定', 'TODO', 'FIXME'). "
                        "Each pattern is a regex. Default: only the built-in patterns.")

    # inventory / questions / MECE
    p.add_argument("--min-inventory", default="auto",
                   help='Minimum item count. With "auto", compute max(50, files_scanned/20).')
    p.add_argument("--max-macro-ratio", type=float, default=0.2)
    p.add_argument("--min-questions", type=int, default=10)
    p.add_argument("--max-open-ratio", type=float, default=0.2)
    p.add_argument("--min-covered-by-fill", type=float, default=None,
                   help='Minimum covered_by fill rate. Default: 0.9 (auto-adjusted for library-sdk template).')
    p.add_argument("--min-mece-coverage", type=float, default=None,
                   help='Minimum MECE coverage rate. Default: 0.7 (auto-adjusted for library-sdk template).')

    # backward compatibility
    p.add_argument("--fail-on-uncovered", action="store_true")
    p.add_argument("--strict", action="store_true")

    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    args.output_dir = args.output_dir or args.specback_dir

    # Resolve template-aware defaults for thresholds
    min_covered_by_fill = _resolve_covered_by_fill(args.specback_dir, args.min_covered_by_fill)
    min_mece_coverage = _resolve_mece_coverage(args.specback_dir, args.min_mece_coverage)

    try:
        report = build_report(
            args.specback_dir,
            output_dir=args.output_dir,
            target_dir_name=args.target_dir_for_required,
            min_inventory=args.min_inventory,
            max_macro_ratio=args.max_macro_ratio,
            min_questions=args.min_questions,
            max_open_ratio=args.max_open_ratio,
            min_covered_by_fill=min_covered_by_fill,
            min_refs_per_chapter=args.min_refs_per_chapter,
            min_lines_per_chapter=args.min_lines_per_chapter,
            min_code_blocks_per_chapter=args.min_code_blocks_per_chapter,
            min_mermaid_per_chapter=args.min_mermaid_per_chapter,
            min_mece_coverage=min_mece_coverage,
            code_block_line_weight=args.code_block_line_weight,
            # New checks for #158
            require_min_body_lines_for_reserved=args.require_min_body_lines_for_reserved,
            forbid_mermaid_styling=args.forbid_mermaid_styling,
            forbid_placeholder_pattern=args.forbid_placeholder_pattern,
            check_mermaid_syntax_flag=args.check_mermaid_syntax_flag,
        )
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    if args.output_format == "json":
        print(render_json(report))
    else:
        print(render_text(report))

    # Missing required files or any gate failure → exit 1.
    if report.missing_required:
        return 1
    if report.gate_failures:
        return 1
    if args.fail_on_uncovered and report.uncovered:
        return 1
    if args.strict and report.naming_warnings:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
