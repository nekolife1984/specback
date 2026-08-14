#!/usr/bin/env python3
"""
data_types.py — Typed envelopes for specback phase output.

Each phase produces a typed ``Envelope`` subclass that declares exactly what
data it passes to the next phase.  This makes the contract between phases
explicit, validated, and self-documenting.

Usage
-----
    from data_types import GoalOutput, ReconOutput, StateTracking

    # Create an envelope
    goal = GoalOutput(
        output_language="en",
        output_dir="specs",
        primary_reader="maintenance_developer",
        ...
    )

    # Serialise / deserialise
    raw = goal.to_dict()
    restored = GoalOutput.from_dict(raw)

    # Compatibility layer
    state_json = goal.to_state_dict()   # → state.json-compatible dict

Design principles
-----------------
1. **stdlib only** — uses ``dataclass``, no pydantic dependency.
2. **Immutable by convention** — envelopes are created, read, serialised,
   never mutated in place.
3. **Backward compatible** — ``to_state_dict()`` / ``from_state_dict()``
   bridge the old ``state.json`` schema.
4. **Self-validating** — each envelope has a ``validate()`` method.
5. **JSON round-trip** — ``to_dict()`` ↔ ``from_dict()`` are symmetric.
"""

from __future__ import annotations

import json
from common import utcnow_iso
from dataclasses import dataclass, field, asdict
from typing import Any, ClassVar, Literal, Optional, get_origin, get_args, get_type_hints


# ===================================================================
# EnvelopeBase
# ===================================================================


@dataclass
class EnvelopeBase:
    """Base class for all phase envelopes.

    Every phase envelope carries a status, a human-readable summary, a
    list of artifact paths it produced, and free-form notes for the next
    phase.
    """

    status: Literal["success", "fail"] = "success"
    summary: str = ""
    artifacts: list[str] = field(default_factory=list)
    notes_for_next_phase: str = ""

    # -- serialisation --------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise this envelope to a plain dict."""
        return _pruned(asdict(self))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Any:
        """Deserialise from a plain dict."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    @classmethod
    def schema(cls) -> dict[str, Any]:
        """Return a JSON Schema draft-07 description of this envelope.

        Useful for tooling that needs to know the shape without importing
        Python (e.g. editor auto-completion, AI prompting).
        """
        return _infer_schema(cls)

    # -- validation -----------------------------------------------------

    def validate(self) -> list[str]:
        """Return a list of validation errors (empty = valid)."""
        return []


# ===================================================================
# Phase 0 — Setup & Goal
# ===================================================================


@dataclass
class GoalOutput(EnvelopeBase):
    """Envelope produced by Phase 0 (Setup & Goal).

    This is the most important envelope in the pipeline — every later
    decision derives from these fields.
    """

    output_language: Literal["en", "ja"] = "en"
    output_dir: str = "specs"
    primary_reader: Literal[
        "maintenance_developer", "delivery_customer", "sme", "regulator"
    ] = "maintenance_developer"
    reader_action: Literal[
        "code_change", "approval_decision", "audit", "learning"
    ] = "code_change"
    granularity: Literal[
        "high_level_overview", "medium", "detailed"
    ] = "medium"
    perspectives: list[str] = field(default_factory=lambda: ["functional_correctness"])
    existing_docs: Literal["none", "update", "coexist", "retire"] = "none"
    free_text_notes: str = ""
    user_custom_deliverables: list[str] = field(default_factory=list)

    # Multi-scope support
    multi_scope: bool = False
    scopes: list[dict[str, str]] = field(default_factory=list)
    current_scope: int = 0

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.output_dir:
            errors.append("output_dir must not be empty")
        if self.multi_scope and not self.scopes:
            errors.append("multi_scope=True but scopes list is empty")
        if self.current_scope < 0:
            errors.append(f"current_scope must be >= 0, got {self.current_scope}")
        return errors

    def to_goal_json(self) -> dict[str, Any]:
        """Return the dict to persist as ``goal.json``."""
        return self.to_dict()


# ===================================================================
# Phase 1 — Recon & Template
# ===================================================================


@dataclass
class ReconOutput(EnvelopeBase):
    """Envelope produced by Phase 1 (Reconnaissance & Template selection)."""

    frameworks: list[str] = field(default_factory=list)
    total_files: int = 0
    template_selected: str = ""
    depth_mode: Literal["comprehensive", "outline", "interactive"] = "comprehensive"
    tree_sitter_available: bool = False
    recon_report_path: str = ""
    customized_chapters: Optional[list[dict[str, Any]]] = None


# ===================================================================
# Phase 2 — Plan & WBS
# ===================================================================


@dataclass
class WBSOutput(EnvelopeBase):
    """Envelope produced by Phase 2 (WBS & Inventory)."""

    chapters: list[dict[str, Any]] = field(default_factory=list)
    inventory_count: int = 0
    inventory_path: str = ""
    wbs_path: str = ""
    source_map_path: str = ""

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.chapters:
            errors.append("chapters list is empty")
        if self.inventory_count <= 0:
            errors.append("inventory_count must be > 0")
        return errors


# ===================================================================
# Phase 3 — Investigate
# ===================================================================


@dataclass
class InvestigateOutput(EnvelopeBase):
    """Envelope produced by Phase 3 (Investigate / Write chapters)."""

    chapters_completed: int = 0
    chapters_blocked: list[str] = field(default_factory=list)
    questions_added: int = 0
    confidence_overall: float = 0.0
    depth_mode_used: str = ""
    drafts_path: str = ""

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.confidence_overall < 0.0 or self.confidence_overall > 1.0:
            errors.append(f"confidence_overall must be 0.0-1.0, got {self.confidence_overall}")
        return errors


# ===================================================================
# Phase 4 — Verify
# ===================================================================


@dataclass
class VerifyOutput(EnvelopeBase):
    """Envelope produced by Phase 4 (Verification)."""

    all_gates_passed: bool = False
    failures: list[str] = field(default_factory=list)
    chapter_metrics: list[dict[str, Any]] = field(default_factory=list)
    mece_coverage_rate: float = 0.0
    coverage_report_path: str = ""

    @property
    def passed(self) -> bool:
        """Convenience alias."""
        return self.all_gates_passed


# ===================================================================
# Phase 5 — Dialogue
# ===================================================================


@dataclass
class DialogueOutput(EnvelopeBase):
    """Envelope produced by Phase 5 (Refine via Dialogue)."""

    questions_resolved: int = 0
    questions_abandoned: int = 0
    open_ratio: float = 0.0
    skip_reason: str = ""
    questions_path: str = ""

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.open_ratio < 0.0 or self.open_ratio > 1.0:
            errors.append(f"open_ratio must be 0.0-1.0, got {self.open_ratio}")
        return errors


# ===================================================================
# Phase 6 — Deliver
# ===================================================================


@dataclass
class DeliverOutput(EnvelopeBase):
    """Envelope produced by Phase 6 (Deliver final spec)."""

    output_path: str = ""
    chapters_delivered: int = 0
    user_custom_delivered: int = 0
    reserved_files_delivered: list[str] = field(default_factory=list)


# ===================================================================
# Phase 6.5 — Interactive Deep-Dive
# ===================================================================


@dataclass
class DeepDiveOutput(EnvelopeBase):
    """Envelope produced by Phase 6.5 (Interactive Deep-Dive)."""

    deep_dives_completed: int = 0
    deep_dive_paths: list[str] = field(default_factory=list)


# ===================================================================
# Phase 7 — Drift Detection & Maintenance
# ===================================================================


@dataclass
class DriftOutput(EnvelopeBase):
    """Envelope produced by Phase 7 (Drift Detection)."""

    affected_sections: int = 0
    impact_high: int = 0
    impact_moderate: int = 0
    impact_low: int = 0
    drift_mode_used: str = ""
    drift_report_path: str = ""


@dataclass
class RefAutofixOutput(EnvelopeBase):
    """Envelope produced by Phase 7b (REF Auto-Fix)."""

    refs_corrected: int = 0
    refs_orphaned: int = 0
    dry_run: bool = True


@dataclass
class ChangeSpecOutput(EnvelopeBase):
    """Envelope produced by Phase 7c (ChangeSpec)."""

    changespec_path: str = ""
    files_changed: int = 0
    breaking_changes: int = 0


@dataclass
class ConfigRefreshOutput(EnvelopeBase):
    """Envelope produced by Phase 7d (Config Refresh)."""

    source_map_entries: int = 0
    trace_sections: int = 0
    commit_hash: str = ""
    hash_mode_updated: bool = False


# ===================================================================
# StateTracking — session lifecycle
# ===================================================================


@dataclass
class StateTracking:
    """Tracks the overall session state — which phase, progress, history.

    This is *not* an envelope (it is not passed between phases).  It is
    the persistent session state that lives in ``state.json`` and drives
    resume behaviour.
    """

    current_phase: int = 0
    phase_progress: dict[str, dict[str, Any]] = field(default_factory=dict)
    started_at: str = ""
    last_updated: str = ""
    session_history: list[dict[str, Any]] = field(default_factory=list)
    all_quality_gates_passed: bool = False

    # ------------------------------------------------------------------
    # session-history helpers
    # ------------------------------------------------------------------

    def record_event(self, event: str, phase: int | None = None) -> None:
        """Append a timestamped event to the session history."""
        now = utcnow_iso()
        self.session_history.append({
            "timestamp": now,
            "phase": phase if phase is not None else self.current_phase,
            "event": event,
        })
        self.last_updated = now

    def advance_phase(self, next_phase: int) -> None:
        """Transition to the next phase and record the event."""
        self.record_event("transitioned", phase=next_phase)
        self.current_phase = next_phase

    # ------------------------------------------------------------------
    # progress helpers
    # ------------------------------------------------------------------

    def init_phase_progress(self, phase: int, total: int) -> None:
        key = f"phase_{phase}"
        if key not in self.phase_progress:
            self.phase_progress[key] = {
                "total_subtasks": total,
                "completed_subtasks": 0,
                "blocked_subtasks": [],
            }

    def complete_subtask(self, phase: int, name: str) -> None:
        key = f"phase_{phase}"
        if key in self.phase_progress:
            self.phase_progress[key]["completed_subtasks"] += 1

    def block_subtask(self, phase: int, name: str) -> None:
        key = f"phase_{phase}"
        if key in self.phase_progress:
            if name not in self.phase_progress[key]["blocked_subtasks"]:
                self.phase_progress[key]["blocked_subtasks"].append(name)

    # ------------------------------------------------------------------
    # serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a ``state.json``-compatible dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StateTracking:
        """Deserialise from a ``state.json``-compatible dict."""
        return cls(**{
            k: v for k, v in data.items()
            if k in cls.__dataclass_fields__
        })

    @classmethod
    def fresh(cls) -> StateTracking:
        """Create a fresh session starting at Phase 0."""
        now = utcnow_iso()
        st = cls(started_at=now, last_updated=now)
        st.record_event("started", phase=0)
        return st


# ===================================================================
# Compatibility layer
# ===================================================================

ENVELOPE_BY_PHASE: dict[int, type[EnvelopeBase]] = {
    0: GoalOutput,
    1: ReconOutput,
    2: WBSOutput,
    3: InvestigateOutput,
    4: VerifyOutput,
    5: DialogueOutput,
    6: DeliverOutput,
    7: DriftOutput,
}

# Alias — string keys for sub-phases, int aliases for convenience
ENVELOPE_MAP: dict[int | str, type[EnvelopeBase]] = {
    0: GoalOutput,
    1: ReconOutput,
    2: WBSOutput,
    3: InvestigateOutput,
    4: VerifyOutput,
    5: DialogueOutput,
    6: DeliverOutput,
    65: DeepDiveOutput,
    7: DriftOutput,
    "7b": RefAutofixOutput,
    "7c": ChangeSpecOutput,
    "7d": ConfigRefreshOutput,
}


def envelope_for_phase(phase: int | str) -> type[EnvelopeBase]:
    """Return the envelope class for a given phase number or key."""
    env = ENVELOPE_MAP.get(phase)
    if env is None:
        raise KeyError(f"No envelope defined for phase={phase!r}")
    return env


def build_persistent_state(
    goal: GoalOutput,
    tracking: StateTracking,
    envelopes: dict[int | str, EnvelopeBase] | None = None,
) -> dict[str, Any]:
    """Build a single dict that can be persisted as the project state.

    This is the compatibility bridge between the old ``state.json``
    (one file containing everything) and the new typed envelope approach.
    """
    result: dict[str, Any] = {
        "goal": goal.to_goal_json(),
        "state": tracking.to_dict(),
    }
    if envelopes:
        result["envelopes"] = {
            str(k): v.to_dict() for k, v in envelopes.items()
        }
    return result


# ===================================================================
# Internal helpers
# ===================================================================


def _pruned(d: dict[str, Any]) -> dict[str, Any]:
    """Remove None values and empty helper fields from serialised output."""
    ignore_keys: set[str] = set()
    # Only strip empty from EnvelopeBase fields, not from domain fields
    return {k: v for k, v in d.items() if v is not None}


def _infer_schema(cls: type[Any]) -> dict[str, Any]:
    """Build a minimal JSON Schema draft-07 description from dataclass fields."""
    properties: dict[str, Any] = {}
    required: list[str] = []
    # Use get_type_hints to resolve string annotations (from __future__ import annotations)
    hints = get_type_hints(cls)
    for f_name, f_field in cls.__dataclass_fields__.items():
        tp = hints.get(f_name, f_field.type)
        desc: dict[str, Any] = {"type": _type_name(tp)}
        # Check for Literal (enum)
        origin = get_origin(tp)
        if origin is Literal:
            desc["enum"] = list(get_args(tp))
        if f_field.default is field and f_field.default_factory is not field:
            pass  # has default factory
        elif f_field.default is not field:
            pass  # has default value
        else:
            required.append(f_name)
        properties[f_name] = desc
    return {
        "title": cls.__name__,
        "type": "object",
        "properties": properties,
        "required": required,
    }


def _type_name(tp: Any) -> str:
    """Map a Python type annotation to a JSON Schema type name."""
    origin = get_origin(tp)
    if origin is list:
        return "array"
    if origin is dict:
        return "object"
    if tp is str:
        return "string"
    if tp is int:
        return "integer"
    if tp is float:
        return "number"
    if tp is bool:
        return "boolean"
    if origin is Literal:
        return "string"
    # Optional[X] → Union[X, None]
    args = get_args(tp)
    if type(None) in args:
        for a in args:
            if a is not type(None):
                return _type_name(a)
    return "string"
