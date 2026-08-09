"""
Tests for coverage-check.py --check-mermaid-syntax feature.

Verifies the static Mermaid syntax checks catch the two common parse
errors that break rendering on GitHub etc.:

1. Unquoted parentheses inside an edge label (|...|)
2. Cylinder node [( ... )] opened but not closed with )]

Quoted labels must NOT produce false positives.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "coverage-check.py"

# Import the module under test (coverage-check.py is a plain script with
# no if-main guard around its function definitions).
import importlib.util

_spec = importlib.util.spec_from_file_location("coverage_check_mermaid", SCRIPT)
cov = importlib.util.module_from_spec(_spec)
sys.modules["coverage_check_mermaid"] = cov
_spec.loader.exec_module(cov)


def _md_with_mermaid(mermaid_block: str) -> dict[str, str]:
    return {"test.md": f"# テスト\n\n```mermaid\n{mermaid_block}\n```\n"}


def test_edge_label_unquoted_parens_detected() -> None:
    """|OpenAIModel (OpenAI互換API)| — unquoted parens must fail."""
    block = (
        "graph TD\n"
        "    E -->|OpenAIModel (OpenAI互換API)| P\n"
    )
    failures = cov.check_mermaid_syntax(_md_with_mermaid(block))
    assert len(failures) == 1
    assert "edge label" in failures[0]


def test_edge_label_quoted_parens_ok() -> None:
    """|"OpenAIModel (OpenAI互換API)"| — quoted parens are legal."""
    block = (
        "graph TD\n"
        '    E -->|"OpenAIModel (OpenAI互換API)"| P\n'
    )
    failures = cov.check_mermaid_syntax(_md_with_mermaid(block))
    assert failures == []


def test_edge_label_function_call_quoted_ok() -> None:
    """|"create_model()"| — quoted parens are legal."""
    block = (
        "flowchart LR\n"
        '    E -->|"create_model()"| P\n'
    )
    failures = cov.check_mermaid_syntax(_md_with_mermaid(block))
    assert failures == []


def test_cylinder_node_unclosed_detected() -> None:
    """DB[(SQLite)<br/>F-004 永続化] — [( not closed with )] must fail."""
    block = (
        "flowchart LR\n"
        "    subgraph DA[データ層]\n"
        "        DB[(SQLite)<br/>F-004 永続化]\n"
        "    end\n"
        "    CH --> DB\n"
    )
    failures = cov.check_mermaid_syntax(_md_with_mermaid(block))
    assert len(failures) == 1
    assert "cylinder" in failures[0]


def test_cylinder_node_closed_ok() -> None:
    """DB[("SQLite<br/>F-004 永続化")] — proper cylinder closure is legal."""
    block = (
        "flowchart LR\n"
        "    subgraph DA[データ層]\n"
        '        DB[("SQLite<br/>F-004 永続化")]\n'
        "    end\n"
        "    CH --> DB\n"
    )
    failures = cov.check_mermaid_syntax(_md_with_mermaid(block))
    assert failures == []


def test_cylinder_node_plain_db_ok() -> None:
    """L[(LLM API)] — plain cylinder without extra text is legal."""
    block = (
        "graph TD\n"
        "    P -->|Ollama / OpenCode Go / Zen / Custom| L[(LLM API)]\n"
    )
    failures = cov.check_mermaid_syntax(_md_with_mermaid(block))
    assert failures == []


def test_node_with_parens_in_quoted_label_ok() -> None:
    """subgraph UI["UI層 (Streamlit)"] — parens inside quoted labels are legal."""
    block = (
        "graph TB\n"
        '    subgraph UI["UI層 (Streamlit)"]\n'
        '        APP["app.py<br/>main()"]\n'
        "    end\n"
        "    APP --> SS\n"
    )
    failures = cov.check_mermaid_syntax(_md_with_mermaid(block))
    assert failures == []


def test_er_diagram_ok() -> None:
    """erDiagram with quoted parens in comments is legal."""
    block = (
        "erDiagram\n"
        '    CONVERSATIONS ||--o{ MESSAGES : "1:N / ON DELETE CASCADE"\n'
        "    CONVERSATIONS {\n"
        '        TEXT id PK "UUID (アプリ側生成)"\n'
        "    }\n"
    )
    failures = cov.check_mermaid_syntax(_md_with_mermaid(block))
    assert failures == []


def test_no_mermaid_blocks_ok() -> None:
    """A chapter without Mermaid produces no syntax failures."""
    failures = cov.check_mermaid_syntax({"test.md": "# テスト\n本文のみ。\n"})
    assert failures == []
