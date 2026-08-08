"""WBS 共通ロジック（ADW 廃止に伴い adws/adw_specback_wbs.py から移設）。

テンプレートからのチャプター抽出・ソースインベントリ生成・gitignore 互換の
除外判定を提供する。Agent-driven ワークフロー（skills/specback/）からも利用できる。
"""

from __future__ import annotations

import re
from pathlib import Path


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
