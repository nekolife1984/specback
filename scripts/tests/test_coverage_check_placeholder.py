"""
Tests for coverage-check.py check_placeholder_patterns (Issue #257).

Verifies that TODO/FIXME and other placeholder patterns are NOT detected
inside fenced code blocks (examples are allowed there), while real
placeholder text in the prose body still fails.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "coverage-check.py"

# Ensure scripts/ is importable — coverage-check.py imports count_refs from
# scripts/refutils.py (Issue #281).
if str(SCRIPT.parent) not in sys.path:
    sys.path.insert(0, str(SCRIPT.parent))

_spec = importlib.util.spec_from_file_location("coverage_check_placeholder", SCRIPT)
assert _spec is not None and _spec.loader is not None
cov = importlib.util.module_from_spec(_spec)
sys.modules["coverage_check_placeholder"] = cov
_spec.loader.exec_module(cov)


def _chapters(content: str) -> dict[str, str]:
    return {"test.md": content}


def test_todo_inside_code_fence_ok() -> None:
    """TODO inside a ```python fence must NOT be flagged (Issue #257)."""
    content = (
        "# Overview\n\n"
        "Some prose.\n\n"
        "```python\n"
        "# TODO: this is an example\n"
        "def f():\n"
        "    pass\n"
        "```\n"
    )
    failures = cov.check_placeholder_patterns(_chapters(content))
    assert failures == [], f"fenced TODO flagged: {failures}"


def test_fixme_inside_code_fence_ok() -> None:
    """FIXME inside a ```bash fence must NOT be flagged."""
    content = (
        "# Deployment\n\n"
        "```bash\n"
        "# FIXME: example command\n"
        "npm install\n"
        "```\n"
    )
    failures = cov.check_placeholder_patterns(_chapters(content))
    assert failures == []


def test_todo_in_prose_fails() -> None:
    """TODO in the prose body must still be flagged."""
    content = "# Overview\n\nTODO: fill this in later\n"
    failures = cov.check_placeholder_patterns(_chapters(content))
    assert len(failures) == 1
    assert "test.md:3" in failures[0]


def test_fixme_in_prose_fails() -> None:
    """FIXME in the prose body must still be flagged."""
    content = "# Overview\n\nThis is FIXME content.\n"
    failures = cov.check_placeholder_patterns(_chapters(content))
    assert len(failures) == 1
    assert "test.md:3" in failures[0]


def test_multiple_fences_toggle_state() -> None:
    """Fence state toggles across multiple blocks (prose after fence is checked)."""
    content = (
        "# Overview\n\n"
        "```python\n"
        "# TODO: example one\n"
        "```\n"
        "\n"
        "TODO: real placeholder in prose\n"
        "\n"
        "```python\n"
        "# TODO: example two\n"
        "```\n"
    )
    failures = cov.check_placeholder_patterns(_chapters(content))
    # Only the prose TODO (line 7) should be flagged.
    assert len(failures) == 1, f"expected 1 failure, got {failures}"
    assert "test.md:7" in failures[0]


def test_language_tagged_fence_ok() -> None:
    """Fences with a language tag (```python) are treated the same."""
    content = "# Overview\n\n```python\n# TODO: example\n```\n"
    failures = cov.check_placeholder_patterns(_chapters(content))
    assert failures == []


def test_extra_patterns_inside_fence_ok() -> None:
    """Custom patterns are also ignored inside fences."""
    content = "# Overview\n\n```text\nHACK: example\n```\n"
    failures = cov.check_placeholder_patterns(
        _chapters(content), extra_patterns=["HACK"],
    )
    assert failures == []


def test_extra_patterns_in_prose_fails() -> None:
    """Custom patterns in prose are flagged."""
    content = "# Overview\n\nHACK: real issue\n"
    failures = cov.check_placeholder_patterns(
        _chapters(content), extra_patterns=["HACK"],
    )
    assert len(failures) == 1
