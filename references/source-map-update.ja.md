# 新しいソースファイル追加時: source-map.json を安全に更新する

spec 管理対象のコードベースに新しい `.py` モジュール（または任意の新規ソースユニット）を追加した場合、`source-map.json` と `inventory.json` に新ユニットを含める必要がある。素朴な方法（`source-map.py` の再実行）は**既存の `<!-- REF: SRC-NNNN -->` マーカーをすべて黙って壊す**ため、避けなければならない。

## ⚠️ 全再生成が危険な理由

`source-map.py` はファイル走査順で SRC-ID を採番する。ファイルを追加すると以降のファイルすべての走査順が変わるため、**既存ユニットの ID がすべてシフトする**（例: 旧 `SRC-0017` = `create_model` が `build_frontmatter` を指すようになる）。

`build-trace.py` は `<!-- REF: SRC-NNNN -->` マーカーを ID で解決し、マーカーの意味的な意図は**検証しない** — 既存 ID は*何かしら*のユニットに解決されるため:

- `covered` 数は変わらない → `mece_passed` が `True` のままになり得る
- 既存の全 REF が黙って別の関数を指す
- ゲート出力には異常が見えないため、`uncovered` 増加よりはるかに危険

## シフトの検出

再生成マップの ID → name 対応を、コミット済みの旧 `specs/trace.json`（git 追跡され、`by_source` が前回のマッピングを保持）と比較する:

```python
python3 -c "
import json
sm = json.load(open('specs/.specback/source-map.json'))
byid = {u['id']: u for u in sm['units']}
old = json.load(open('specs/trace.json'))['by_source']
for uid in old:
    print(uid, 'OLD:', old[uid]['name'], '-> NEW:', byid.get(uid, {}).get('name'))
"
```

`NEW` の name が `OLD` と異なる場合、その ID の REF はすべて無効になっている。

## 正しい手順: 旧 ID 復元 + 新ユニット末尾追加

すべてを再生成してはならない。コミット済みの `specs/trace.json` から旧ユニットを復元し、新モジュールのユニットだけを末尾に新しい ID で追加する。

```bash
# 1) コミット済み specs/trace.json から旧ユニットを復元 + 新ユニットを追加
python3 scripts/restore-sourcemap-from-trace.py \
  --repo <codebase> \
  --new-ids SRC-0014,SRC-0015,... \  # 再生成マップで新モジュールに割り当てられた ID
  --apply                            # 復元マップを書き込む（既定は dry-run）

# 2) 復元した source-map から必ず inventory.json を再生成
python3 scripts/build-inventory-from-sourcemap.py \
  --source-map <codebase>/specs/.specback/source-map.json \
  --output <codebase>/specs/.specback/inventory.json
```

復元スクリプトは再生成された `source-map.json` から新ユニットのメタデータ（path / line_range / kind / name）を取得し、`old_max_id + 1` から再採番する。

### 安全性の挙動（Issue #247 で追加）

- **既定 dry-run** — `--apply` を付けなければ何をやるかの表示のみで、マップは変更されない。
- **バックアップ + アトミック書込** — `--apply` は `source-map.json.pre-restore` をマップの隣に保存し、テンポラリファイルを書いてから `os.replace` で置き換える。唯一のコピーがその場で切り詰められることはない。
- **再実行は拒否** — 復元済みマップには `"restored_from": "trace.json"` が付く。このマップに対して再実行すると失敗する（再実行は追加ユニットの同一性を入れ替えるため）。再生成マップが本当に新しい場合のみ `--force` を渡す。
- **`--new-ids` を検証** — ID は `SRC-NNNN` 形式で、再生成マップに存在し、旧ユニットの ID と衝突してはならない。不明な ID はサイレントなデータロスではなくハードエラーになる。
- **fingerprint 警告** — trace.json に fingerprint が無いため旧ユニットを fingerprint なしで復元した場合、そのユニットの drift 検出が弱まる旨の警告を出す。
- **クリーンなエラー** — trace.json / source-map.json の欠落・空・形式不正はトレースバックではなく `ERROR:` メッセージで終了する。

### 罠

- **inventory 再生成をスキップすると coverage-check が FAIL する** — 復元済み source-map（例: 47 units）と古い inventory（53 units）が一致しないと、`INV-NNNN.related_source_ids contains 'SRC-NNNN' which is not in source-map.json` のゲート失敗になる。必ず同じ source-map から inventory を再構築する。
- **新規執筆の仕様書セクションは追加後の ID を使う** — 再生成（誤った）ID で新章（例: F-008）を書いた場合、復元後に REF が誤ったユニットを指す。追加後の ID リストと突き合わせ、そのセクションの行範囲内のみ sed 置換する。
- **`tests/**` と `specs/**` は全再生成の exclude-globs に含める** — デフォルト除外（`.venv` 等）はこれらをカバーしない。フルスキャンすると全テスト関数と spec ディレクトリ自体が取り込まれる（ai-chat で units が 33 → 180 に増加した実績）。
- **新ユニットは参照されるまで uncovered** — 新章が REF で参照するまで、新モジュールの関数は uncovered として表示される。章を書く間に全 SRC-ID を埋めること。

## 実例

ai-chat Issue #29 / F-008 で `wiki_ui.py`（14 関数）を追加。旧 33 ユニット + 新 14 = 47 ユニット。復元と REF 記入後: `uncovered=0`、`mece_passed=True`、coverage-check `gate_failures=[]`。
