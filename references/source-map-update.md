# Adding a new source file: updating source-map.json safely

When a new `.py` module (or any new source unit) is added to a spec-managed
codebase, `source-map.json` and `inventory.json` must include the new units.
The naive approach — re-running `source-map.py` — **silently breaks every
existing `<!-- REF: SRC-NNNN -->` marker** and must be avoided.

## ⚠️ Why a full re-run is dangerous

`source-map.py` numbers SRC-IDs in file-scan order. Adding a file changes the
scan order for every subsequent file, so **all existing unit IDs shift** (e.g.
old `SRC-0017` = `create_model` may now point at `build_frontmatter`).

`build-trace.py` resolves a `<!-- REF: SRC-NNNN -->` marker by ID and does
**not** verify the marker's semantic intent — any existing ID resolves to
*some* unit, so:

- `covered` counts stay the same → `mece_passed` can still be `True`
- Every existing REF silently points at the wrong function
- This is far more dangerous than an `uncovered` increase, because nothing
  looks wrong in the gate output

## Detecting the shift

Compare the re-generated map's ID → name mapping against the committed old
`specs/trace.json` (it is git-tracked and its `by_source` holds the previous
mapping):

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

If any `NEW` name differs from the `OLD` name, every REF with that ID is now
invalid.

## Correct procedure: restore old IDs + append new units

Do **not** regenerate everything. Restore the old units from the committed
`specs/trace.json` and append only the new module's units at the end with
fresh IDs.

```bash
# 1) Restore old units from committed specs/trace.json + append new units
python3 scripts/restore-sourcemap-from-trace.py \
  --repo <codebase> \
  --new-ids SRC-0014,SRC-0015,...   # IDs the new module got in the re-generated map

# 2) ALWAYS regenerate inventory.json from the restored source-map
python3 scripts/build-inventory-from-sourcemap.py \
  --source-map <codebase>/specs/.specback/source-map.json \
  --output <codebase>/specs/.specback/inventory.json
```

The restore script reads the re-generated `source-map.json` for the new
units' metadata (path / line_range / kind / name) and renumbers them from
`old_max_id + 1`.

### Traps

- **Skipping the inventory regeneration FAILs coverage-check** — a restored
  source-map (e.g. 47 units) with a stale inventory (53 units) produces
  `INV-NNNN.related_source_ids contains 'SRC-NNNN' which is not in source-map.json`
  gate failures. Always rebuild inventory from the same source-map.
- **Newly written spec sections must use the appended IDs** — if you wrote a
  new chapter (e.g. F-008) using the re-generated (wrong) IDs, its REFs point
  at wrong units after restoration. Match them against the appended ID list
  and sed-replace within that section's line range only.
- **`tests/**` and `specs/**` must be in exclude-globs for any full re-run** —
  the default excludes (`.venv` etc.) do not cover them; a full scan will
  pull in every test function and the spec dir itself (observed: units jumped
  33 → 180 in ai-chat).
- **New units are uncovered until referenced** — the new module's functions
  show up as uncovered until the new spec chapter REFs them. Fill in all new
  SRC-IDs while writing the chapter.

## Real case

ai-chat Issue #29 / F-008 added `wiki_ui.py` (14 functions). Old 33 units +
14 new = 47 units; after restoration and REF fill-in: `uncovered=0`,
`mece_passed=True`, coverage-check `gate_failures=[]`.
