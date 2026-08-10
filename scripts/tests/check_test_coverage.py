#!/usr/bin/env python3
"""Check that every public function/class in a script has a corresponding test.

Usage:
    python scripts/tests/check_test_coverage.py <script.py> [<test.py>]

If <test.py> is omitted, it defaults to tests/test_<script_basename>.py.

Exit codes:
    0 — all public symbols have test coverage
    1 — some symbols are missing test coverage
    2 — file not found or parse error
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

# Symbols exempt from test coverage requirements (always OK to skip)
EXEMPT_SYMBOLS: set[str] = {
    # Entry points
    "main",
    # Test infrastructure
    "conftest",
    # Dunder methods on classes (tested indirectly)
    "__init__", "__str__", "__repr__", "__enter__", "__exit__",
    "__len__", "__iter__", "__getitem__", "__setitem__",
    "__call__", "__hash__", "__eq__", "__ne__",
}


def get_public_symbols(filepath: Path) -> set[str]:
    """Extract names of TOP-LEVEL public functions, classes, and async functions.

    Nested functions (e.g. ``id_factory`` inside ``build_source_map``) are
    exercised through their enclosing function's tests, so they are not
    reported — walking every node with ``ast.walk`` produced false
    "missing coverage" reports for them.
    """
    try:
        tree = ast.parse(filepath.read_text(encoding="utf-8"))
    except SyntaxError as e:
        print(f"ERROR: Syntax error in {filepath}: {e}", file=sys.stderr)
        sys.exit(2)
    except FileNotFoundError:
        print(f"ERROR: File not found: {filepath}", file=sys.stderr)
        sys.exit(2)

    symbols: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                symbols.add(node.name)
        elif isinstance(node, ast.ClassDef):
            if not node.name.startswith("_"):
                symbols.add(node.name)
    return symbols


def get_test_symbols(filepath: Path) -> set[str]:
    """Extract names of test functions/classes from a test file."""
    try:
        tree = ast.parse(filepath.read_text(encoding="utf-8"))
    except (SyntaxError, FileNotFoundError):
        return set()

    symbols: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.add(node.name)
        elif isinstance(node, ast.ClassDef):
            symbols.add(node.name)
    return symbols


def get_test_files(script_path: Path) -> list[Path]:
    """Find ALL test files for a script (split-test support).

    Coverage-check.py is tested across multiple files
    (test_coverage_check_code_blocks.py, test_coverage_check_core.py, …);
    matching only ``test_<name>.py`` produced false "missing" reports.
    """
    base = script_path.stem.replace("-", "_")
    tests_dir = script_path.parent / "tests"
    if not tests_dir.is_dir():
        return []
    return sorted(tests_dir.glob(f"test_{base}*.py"))


def _is_covered(sym: str, test_files: list[Path]) -> bool:
    """A symbol is covered if a candidate test name exists OR the symbol is
    referenced in any test file (e.g. ``drift.analyze_impact(...)``)."""
    candidates = set(_test_name_candidates(sym))
    for tf in test_files:
        if any(c in get_test_symbols(tf) for c in candidates):
            return True
        try:
            content = tf.read_text(encoding="utf-8")
        except OSError:
            continue
        # Reference-based: symbol used via module attribute or bare call.
        # ``import gates`` alone does NOT match — the module name differs
        # from the public symbols of the script.
        if re.search(rf"\b{sym}\b", content):
            return True
    return False


def _test_name_candidates(symbol: str) -> list[str]:
    """Generate possible test names for a given symbol.

    Examples:
        build_report -> [test_build_report]
        hash_line_range -> [test_hash_line_range, test_hash_line, test_hash]
    """
    candidates = [f"test_{symbol}"]
    # For compound names like load_source_map, also check test_source_map
    parts: list[str] = []
    current: list[str] = []
    for i, ch in enumerate(symbol):
        if ch.isupper() and current:
            parts.append("".join(current))
            current = [ch.lower()]
        else:
            current.append(ch)
    if current:
        parts.append("".join(current))
    if len(parts) > 1:
        for part in parts[1:]:
            candidates.append(f"test_{part}")
    return candidates


def check_coverage(script_path: Path, test_path: Path) -> int:
    """Return 0 if coverage is adequate, 1 if symbols are missing.

    ``test_path`` may be a single file (explicit CLI arg) or a directory of
    split test files; all matching ``test_<name>*`` files are considered.
    """
    script_syms = get_public_symbols(script_path)

    if test_path.is_dir():
        test_files = get_test_files(script_path)
    else:
        # Single explicit test file, or the resolved default.
        test_files = [test_path] if test_path.exists() else get_test_files(script_path)
        if not test_files and test_path.suffix == ".py":
            test_files = [test_path]

    missing: list[str] = []
    for sym in sorted(script_syms):
        if sym in EXEMPT_SYMBOLS:
            continue
        if not _is_covered(sym, test_files):
            missing.append(sym)

    if not missing:
        return 0

    # Only report top-level symbols (skip nested function names for brevity)
    print(f"⚠️  Missing test coverage for: {script_path.name}")
    for sym in missing:
        print(f"   - {sym}")
    print(f"   Expected in: {test_path}")
    return 1


def main() -> int:
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <script.py> [<test.py>]", file=sys.stderr)
        return 2

    script_path = Path(sys.argv[1]).resolve()
    if not script_path.exists():
        print(f"ERROR: {script_path} not found", file=sys.stderr)
        return 2

    if len(sys.argv) >= 3:
        test_path = Path(sys.argv[2]).resolve()
    else:
        # Default: tests/ directory — all test_<name>* files are collected.
        test_path = script_path.parent / "tests"

    return check_coverage(script_path, test_path)


if __name__ == "__main__":
    sys.exit(main())
