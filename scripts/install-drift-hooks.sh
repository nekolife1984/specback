#!/bin/sh
# scripts/install-drift-hooks.sh — Install an opt-in pre-push drift hook.
#
# Issue #266: 対象プロジェクト向けの opt-in ドリフト検出フック。
# 初期値は warn モード（ドリフトを検出しても push 自体はブロックしない）。
#
# 動作:
#   .git/hooks/pre-push を生成する。既存の pre-push（symlink 含む）は
#   pre-push.specback-backup に退避し、既存フックを連鎖実行するラッパーを
#   生成する（既存フックを壊さない）。
#
# 対象は「実行した場所（cwd）」のリポジトリ。specback の scripts/ は
# どこからでも参照できる（このスクリプトの場所から解決）。
#
# 使い方:
#   cd /path/to/target-project && sh /path/to/specback/scripts/install-drift-hooks.sh
#   SPECBACK_FAIL_ON_DRIFT=1 を付けると fail モード（ドリフトで push をブロック）
set -e

# このスクリプトの場所（specback の scripts/）
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
GATE_PY="$SCRIPT_DIR/specback-gate.py"

# 対象は実行場所（cwd）のリポジトリ
TARGET="$(pwd)"
HOOK_DIR="$TARGET/.git/hooks"
HOOK_DST="$HOOK_DIR/pre-push"
BACKUP="$HOOK_DIR/pre-push.specback-backup"

if [ ! -f "$GATE_PY" ]; then
  echo "❌ specback-gate.py not found at $GATE_PY" >&2
  exit 1
fi

if [ ! -d "$TARGET/.git" ]; then
  echo "❌ Not a git repository: $TARGET" >&2
  exit 1
fi

mkdir -p "$HOOK_DIR"

# 既存 pre-push を退避（symlink はリンク自体を、実ファイルは実体をコピー）
if [ -e "$HOOK_DST" ] && [ ! -e "$BACKUP" ]; then
  if [ -L "$HOOK_DST" ]; then
    # symlink の場合: リンク先を実体としてコピーし、リンク自体は破棄
    LINK_TARGET="$(readlink "$HOOK_DST")"
    cp -P "$HOOK_DST" "$BACKUP"
    rm -f "$HOOK_DST"
    # バックアップにリンク先パスを記録して復元を容易に
    echo "$LINK_TARGET" > "$HOOK_DIR/pre-push.specback-link-target"
  else
    cp "$HOOK_DST" "$BACKUP"
  fi
  echo "  ⚠  Backed up existing pre-push hook to pre-push.specback-backup"
fi

# フック本体を生成（既存フックがある場合は連鎖実行）
SPECBACK_FAIL_ON_DRIFT="${SPECBACK_FAIL_ON_DRIFT:-0}"
{
  echo "#!/bin/sh"
  echo "# specback-drift pre-push hook (opt-in, Issue #266) — auto-generated"
  echo "# Uninstall: rm $HOOK_DST && mv $BACKUP $HOOK_DST"
  echo ""
  echo "SPECBACK_FAIL_ON_DRIFT='$SPECBACK_FAIL_ON_DRIFT'"
  echo ""
  echo "# 既存フックの連鎖実行（バックアップが存在する場合）"
  echo "if [ -f \"$BACKUP\" ]; then"
  echo "  sh \"$BACKUP\" \"\$@\" || exit \$?"
  echo "fi"
  echo ""
  echo "# ドリフト検出（warn モード初期値: ブロックしない）"
  echo "GATE_PY='$GATE_PY'"
  echo "echo \"🔎 specback-drift: checking for spec drift...\""
  echo "if [ \"\$SPECBACK_FAIL_ON_DRIFT\" = \"1\" ]; then"
  echo "  python3 \"\$GATE_PY\" --ci --specback-dir .specback || {"
  echo "    echo \"❌ specback drift detected — update the spec before pushing.\""
  echo "    echo \"   To bypass: git push --no-verify\""
  echo "    exit 1"
  echo "  }"
  echo "else"
  echo "  python3 \"\$GATE_PY\" --ci --warn-only --specback-dir .specback || true"
  echo "fi"
  echo "echo \"✅ specback-drift: done\""
} > "$HOOK_DST"
chmod +x "$HOOK_DST"

echo ""
echo "✅ Installed specback-drift pre-push hook (target: $TARGET):"
echo "   - warn モード（初期値）: ドリフトを検出しても push をブロックしません"
echo "   - fail モードに変更: SPECBACK_FAIL_ON_DRIFT=1 で再実行"
echo "   - アンインストール: rm $HOOK_DST && mv $BACKUP $HOOK_DST"
echo ""
