# Skills-in-the-DB Audit + Eval Flapper Diagnosis (proven 2026-07-01)

## Skills are NOT embedded in Supabase

When asked to "audit skills in the DB": there are **zero skill-content rows** in the `knowledge` table. The June 2026 taxonomy cron wrote `sector_path` into SKILL.md **frontmatter**; `unified_search._search_skills()` globs `skills/**/SKILL.md` live from disk per query and scores keyword-match × Fibonacci sector boost. The audit surface is therefore frontmatter + the search path, not DB rows.

Three failure classes found and fixed 2026-07-01:

### 1. `.curator_backups` glob leak (critical)
The glob picked up `.curator_backups/` — 25 deleted skill copies were searchable, and the deleted honcho skill ranked **#1 (score 1.00)** above every live skill for a memory query. Since `prefetch()` → `unified_search`, deleted skills could leak into per-turn auto-recall. Fixed with a skip-filter in `_search_skills` (`if '.curator_backups' in str(skill_md): continue`).

**Rule:** any new store scanner added to `unified_search` MUST exclude `.curator_backups` and `.bak-*` paths. Verify with the smoking-gun probe: search for a term unique to a deleted skill and confirm zero BACKUP-path hits.

### 2. Orphan sector_paths in frontmatter
The classifier invented plausible-but-nonexistent taxonomy nodes (`development/quality`, `user/design`, `memory/retrieval`). **36 of 77** classified skills had paths not present in `scripts/knowledge/taxonomy.json`.

Validation one-liner:
```python
import json
t = json.load(open('scripts/knowledge/taxonomy.json'))
valid = set(tuple(n['path']) for n in t['nodes'])   # nodes carry 'path', not 'name'
```
Remap orphans to the nearest REAL node. Prefer an honest level-1 parent (e.g. `development`) over inventing a leaf. Use per-skill overrides when the blanket path-map is wrong for one skill (dogfood → `development/webui`, not the generic mapping). Backup each SKILL.md before rewriting the frontmatter line; verify all frontmatters still `yaml.safe_load` clean afterward.

### 3. Corrupted sector_path rows in Supabase
A string passed where a list was expected stores character-split paths (`h/e/r/m/e/s///a/g/e/n/t/...`). Detect:
```python
bad = [r for r in rows if r.get('sector_path')
       and any(len(seg)==1 for seg in r['sector_path']) and len(r['sector_path'])>5]
```
Fix via REST PATCH — **no re-embed needed** (sector_path doesn't touch the vector).

## Eval failure triage now has THREE bins, not two

The established doctrine (stale ground-truth vs coverage gap) gained a third class:

**Duplicate-cluster ranking jitter.** A query that FAILS in `eval_benchmark()` but hits **rank 0 / score 1.0** on a direct `search()` probe of the identical query is nondeterministic ranking flap, not a gap. Root cause observed: 9 rows mentioned Mealio (3 near-identical facts stored on consecutive days + 3 kitchen-sink "fleet repos" rows); MMR/rerank tie-breaking flips which row surfaces between runs.

Tells:
- direct probe passes, eval fails
- the failing query CHANGES between consecutive eval runs
- grep the store for the topic term: >3 same-topic rows with overlapping text = the cluster

Fix is **hygiene** (collapse to 1 canonical fact + 1 pointer via DELETE, backup first), NOT another alias fact — adding facts to a jittery cluster makes the jitter worse.

Coverage-gap fixes (the second bin) confirmed again: DELETE + RE-STORE with query-shaped aliases appended ("The machine that runs Hermes: Mac Mini...", "knowledge.py is the CLI script for store and search commands...") took P@5 96% → 98% in one pass.

## Delegation verification note

Fan-out fix workers self-reported "backups created for all N files" while silently leaving several unpatched (iteration cap). The completion grep over the ACTUAL files — not the worker summary — is what caught it. Never mark a fan-out batch done on self-reports; run the verification sweep yourself.
