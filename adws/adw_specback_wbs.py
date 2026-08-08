#!/usr/bin/env python3
"""ADW — Phase 2: WBS (Work Breakdown Structure).

Hybrid code + agent + code ADW. Generates the chapter list from the selected
template, delegates WBS planning to sub-agents, and produces an inventory.

Usage:
    uv run adws/adw_specback_wbs.py --target /path/to/codebase
    uv run adws/adw_specback_wbs.py --target /path --goal /path/.specback/goal.json
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from adws.adw_modules import session  # noqa: E402
from adws.adw_modules.utils import (  # noqa: E402
    add_common_args,
    resolve_specback_dir,
)
from scripts.data_types import GoalOutput, ReconOutput, WBSOutput  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="specback ADW — Phase 2: WBS"
    )
    add_common_args(parser)
    parser.add_argument(
        "--output-dir", type=str, default=None,
        help="Output directory (default: current dir)",
    )
    parser.add_argument("--goal", type=str, default=None, help="Path to goal.json")
    parser.add_argument("--recon", type=str, default=None, help="Path to ReconOutput envelope JSON")
    parser.add_argument("--non-interactive", action="store_true", help="Skip interactive prompts")
    parser.add_argument("--envelope-out", type=str, default=None, help="Path to write the WBSOutput envelope JSON")
    return parser


def load_env(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# --- gitignore 互換パターン（stdlib only） ---

def load_gitignore_patterns(target: Path) -> list[tuple[re.Pattern[str], bool]]:
    """target 直下の .gitignore / .ignore を読み込み (パターン, 否定フラグ) リストを返す。

    - `.gitignore` を先に読み、`.ignore` のパターンを後ろに追加する（後者が優先される）
    - `#` コメント行・空行はスキップ
    - `!` で始まる行は否定（再include）パターン
    - パターンは fnmatch 互換の正規表現に変換する
    """
    patterns: list[tuple[re.Pattern[str], bool]] = []
    for fname in (".gitignore", ".ignore"):
        path = target / fname
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            negate = line.startswith("!")
            if negate:
                line = line[1:].strip()
            if not line:
                continue
            # 先頭スラッシュはルート相対を意味するが、fnmatch は相対パス全体に
            # マッチさせるので、正規表現化で扱う
            regex = _gitignore_pattern_to_regex(line)
            patterns.append((re.compile(regex), negate))
    return patterns


def _gitignore_pattern_to_regex(pattern: str) -> str:
    """gitignore パターンを fnmatch 互換の正規表現文字列に変換する。

    - `**/` → 任意の深さのディレクトリ（`(?:.*/)?`）
    - `*` → `/` 以外の任意文字列
    - `?` → `/` 以外の任意1文字
    - 末尾 `/` → ディレクトリのみ（パスのどの位置にもマッチさせるため `(?:$|/)` 相当にする）
    - 先頭 `/` → ルート相対（プレフィックスを除去して相対パス全体にマッチ）
    """
    p = pattern
    anchored = p.startswith("/")
    if anchored:
        p = p[1:]
    dir_only = p.endswith("/")
    if dir_only:
        p = p[:-1]

    # fnmatch で扱いやすいよう ** を特殊処理
    parts: list[str] = []
    i = 0
    while i < len(p):
        if p[i : i + 3] == "**/":
            parts.append("(?:.*/)?")
            i += 3
        elif p[i : i + 2] == "**":
            parts.append(".*")
            i += 2
        else:
            # 1文字ずつ処理
            ch = p[i]
            if ch == "*":
                parts.append("[^/]*")
            elif ch == "?":
                parts.append("[^/]")
            elif ch == ".":
                parts.append(r"\.")
            elif ch == "[":
                # 文字クラスはそのまま（簡易対応）
                j = i + 1
                while j < len(p) and p[j] != "]":
                    j += 1
                if j < len(p):
                    parts.append(p[i : j + 1])
                    i = j
                else:
                    parts.append(r"\[")
            else:
                parts.append(re.escape(ch))
            i += 1

    body = "".join(parts)
    # スラッシュを含まないパターンはファイル名・ディレクトリ名の両方にマッチ
    # （例: build → build/out.py にもマッチ）。末尾 / のディレクトリ指定も同様。
    has_slash = "/" in p
    if dir_only or not has_slash:
        suffix = "(?:/.*)?$"
    else:
        suffix = "$"
    if anchored:
        return rf"^{body}{suffix}"
    # 非アンカーはパスのどの位置のセグメントにもマッチ（git の挙動に近づける）
    return rf"(?:^|/){body}{suffix}"


def is_ignored(rel_path: str, patterns: list[tuple[re.Pattern[str], bool]]) -> bool:
    """gitignore パターン群に対してパスが無視されるか判定する。

    後から読み込んだパターン（.ignore）が優先される。否定パターン（!）が
    最後にマッチした場合は無視しない（再include）。
    """
    ignored = False
    for pat, negate in patterns:
        if pat.search(rel_path):
            ignored = not negate
    return ignored


def parse_chapters_from_template(template_path: Path) -> list[dict[str, str]]:
    """Parse chapter headings from a template markdown file."""
    chapters: list[dict[str, str]] = []
    if not template_path.exists():
        return chapters

    content = template_path.read_text(encoding="utf-8")
    # h3 の "### Chapter N: Title" のみが実際のチャプター。
    # h2（"## Chapter outline" 等のセクション名）は誤検出を避けるため除外する。
    heading_pattern = re.compile(r"^###\s+Chapter\s+\d+:\s*(.+)$", re.MULTILINE)
    reserved = {"00-metadata.md": "Metadata", "99-unresolved.md": "Unresolved Items", "traceability.md": "Traceability"}

    for fname, title in reserved.items():
        chapters.append({"filename": fname, "title": title, "kind": "reserved"})

    for match in heading_pattern.finditer(content):
        heading_text = match.group(1).strip()
        if heading_text.startswith("#") or not heading_text:
            continue
        slug = heading_text.lower()
        slug = re.sub(r"[^a-z0-9\s-]", "", slug)
        slug = re.sub(r"\s+", "-", slug.strip())
        slug = re.sub(r"-+", "-", slug)
        fname = f"{slug}.md"
        if fname in reserved:
            continue
        chapters.append({"filename": fname, "title": heading_text, "kind": "standard"})

    return chapters


def generate_inventory(target: Path) -> list[dict[str, str]]:
    """Generate a basic source inventory from the target codebase."""
    inventory: list[dict[str, str]] = []
    ext_map = {
        ".py": "source", ".js": "source", ".ts": "source", ".tsx": "source", ".jsx": "source",
        ".java": "source", ".go": "source", ".rs": "source", ".rb": "source", ".php": "source",
        ".cs": "source", ".swift": "source", ".kt": "source", ".cpp": "source", ".c": "source",
        ".h": "header", ".hpp": "header", ".sql": "data",
        ".yaml": "config", ".yml": "config", ".json": "config", ".toml": "config", ".xml": "config",
        ".md": "doc", ".rst": "doc", ".css": "style", ".scss": "style", ".html": "template",
        ".sh": "script",
    }
    roles = {"source": "implementation", "header": "interface", "config": "configuration",
             "data": "data", "doc": "documentation", "style": "presentation",
             "template": "presentation", "script": "build"}

    # スキャン除外ディレクトリ（仮想環境・VCS・キャッシュ・生成物）
    excluded_dirs = {
        ".specback", "node_modules", ".venv", "venv", ".git",
        "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
        ".mypy", ".egg-info", "dist", "build", "htmlcov",
    }

    # .gitignore / .ignore パターン（プロジェクト固有の除外設定）
    ignore_patterns = load_gitignore_patterns(target)

    patterns = ["**/*.py", "**/*.js", "**/*.ts", "**/*.java", "**/*.go", "**/*.rs",
                "**/*.rb", "**/*.php", "**/*.cs", "**/*.swift", "**/*.kt",
                "**/*.cpp", "**/*.c", "**/*.h", "**/*.sql",
                "**/*.yaml", "**/*.yml", "**/*.json", "**/*.toml", "**/*.xml",
                "**/*.md", "**/*.css", "**/*.html", "**/*.sh"]
    for pattern in patterns:
        for f in target.rglob(pattern):
            if f.is_file() and not any(p in excluded_dirs for p in f.parts):
                rel = str(f.relative_to(target))
                if is_ignored(rel, ignore_patterns):
                    continue
                ext = f.suffix.lower()
                ft = ext_map.get(ext, "other")
                inventory.append({"file": rel, "type": ft, "role": roles.get(ft, "other")})
                if len(inventory) >= 2000:
                    break
        if len(inventory) >= 2000:
            break
    return inventory


def run_wbs(target: Path, output_dir: Path, goal: GoalOutput | None = None,
            recon: ReconOutput | None = None, non_interactive: bool = False) -> WBSOutput:
    """Execute Phase 2 WBS and return a WBSOutput envelope."""
    specback_dir = resolve_specback_dir(str(target), str(output_dir / ".specback") if output_dir else None)
    specback_dir.mkdir(parents=True, exist_ok=True)

    template_name = "web-app"
    if recon:
        template_name = recon.template_selected
    elif goal:
        template_name = getattr(goal, "template", "web-app")

    template_path = _PROJECT_ROOT / "templates" / f"{template_name}.md"
    chapters = parse_chapters_from_template(template_path)
    if not chapters:
        chapters = [
            {"filename": "00-metadata.md", "title": "Metadata", "kind": "reserved"},
            {"filename": "01-overview.md", "title": "System Overview", "kind": "standard"},
            {"filename": "02-architecture.md", "title": "System Architecture", "kind": "standard"},
            {"filename": "99-unresolved.md", "title": "Unresolved Items", "kind": "reserved"},
            {"filename": "traceability.md", "title": "Traceability", "kind": "reserved"},
        ]

    print(f"  📋 {len(chapters)} chapter(s) identified")
    inventory = generate_inventory(target)
    print(f"  📦 {len(inventory)} source units inventoried")

    wbs_data = {"template": template_name, "chapters": chapters,
                "inventory_count": len(inventory), "generated_at": datetime.utcnow().isoformat()}
    wbs_path = specback_dir / "wbs.json"
    wbs_path.write_text(json.dumps(wbs_data, ensure_ascii=False, indent=2), encoding="utf-8")

    inv_path = specback_dir / "inventory.json"
    inv_path.write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")

    drafts_dir = specback_dir / "drafts"
    drafts_dir.mkdir(parents=True, exist_ok=True)
    for ch in chapters:
        draft_file = drafts_dir / ch["filename"]
        if not draft_file.exists():
            title = ch["title"]
            body = f"# {title}\n\n<!-- Chapter: {ch['filename']} -->\n\n"
            draft_file.write_text(body, encoding="utf-8")

    return WBSOutput(chapters=[ch for ch in chapters],
                     inventory_count=len(inventory),
                     inventory_path=str(inv_path),
                     wbs_path=str(wbs_path))


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    target = Path(args.target).resolve()
    if not target.is_dir():
        print(f"Error: target directory not found: {target}", file=sys.stderr)
        return 1
    output_dir = Path(args.output_dir or ".").resolve()

    goal = GoalOutput.from_dict(load_env(args.goal)) if args.goal else None
    recon = None
    if args.recon:
        rd = load_env(args.recon)
        recon = ReconOutput(template_selected=rd.get("template_selected", "web-app"),
                            report_path=rd.get("report_path", ""),
                            languages_found=rd.get("languages_found", []),
                            estimated_complexity=rd.get("estimated_complexity", "medium"),
                            multi_scope_detected=rd.get("multi_scope_detected", False),
                            scopes=rd.get("scopes", []))

    run = session.ensure(adw_id=args.adw_id)
    with run.phase(session.PhaseParams(name="wbs", kind="code", owner="code",
                                        description="Work breakdown structure generation")) as ph:
        envelope = run_wbs(target=target, output_dir=output_dir, goal=goal, recon=recon, non_interactive=args.non_interactive)
        ph.log(envelope=envelope.to_dict())
        if args.envelope_out:
            Path(args.envelope_out).write_text(json.dumps(envelope.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n  ✅ WBS complete: {len(envelope.chapters)} chapters")
        return run.finish(accepted=True)


if __name__ == "__main__":
    sys.exit(main())
