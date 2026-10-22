"""Dump the live knowledge-store schema (table, indexes, RPC functions, policies) from Supabase via the Management API
into scripts/knowledge/sql/knowledge.sql as an idempotent migration. Read-only against the database."""
import json, os, re, sys, urllib.request
H = os.path.expanduser("~/.hermes")
env = dict(l.strip().split("=", 1) for l in open(f"{H}/.env") if "=" in l and not l.startswith("#"))
tok = env["SUPABASE_ACCESS_TOKEN"].strip().strip('"'); ref = re.search(r"https://([a-z]+)\.supabase\.co", env["SUPABASE_URL"]).group(1)

def q(sql):
    req = urllib.request.Request(f"https://api.supabase.com/v1/projects/{ref}/database/query", data=json.dumps({"query": sql}).encode(),
                                 headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())

cols = q("""select column_name, data_type, udt_name, character_maximum_length, is_nullable, column_default,
            coalesce(a.atttypmod, -1) typmod, format_type(a.atttypid, a.atttypmod) ftype
            from information_schema.columns c join pg_attribute a on a.attrelid='public.knowledge'::regclass and a.attname=c.column_name
            where table_schema='public' and table_name='knowledge' order by ordinal_position""")
idx = q("select indexname, indexdef from pg_indexes where schemaname='public' and tablename='knowledge' order by indexname")
fns = q("""select p.proname, pg_get_function_identity_arguments(p.oid) args, pg_get_functiondef(p.oid) def
           from pg_proc p join pg_namespace n on n.oid=p.pronamespace
           where n.nspname='public' and (p.proname like 'match_knowledge%' or p.proname like '%knowledge%' or p.proname like '\_meta\_%') order by p.proname""")
pol = q("select policyname, cmd, roles, qual, with_check from pg_policies where schemaname='public' and tablename='knowledge'")
rls = q("select relrowsecurity from pg_class where oid='public.knowledge'::regclass")
trg = q("select tgname, pg_get_triggerdef(t.oid) def from pg_trigger t where tgrelid='public.knowledge'::regclass and not tgisinternal")
ext = q("select extname, extversion from pg_extension where extname in ('vector','pg_net','pg_trgm')")

out = ["-- Knowledge store schema, dumped from the live project on 2026-10-21 (scripts/knowledge/sql/dump_knowledge_schema.py).",
       "-- Idempotent: safe to re-run. Apply via the Supabase Management API or the SQL editor, then NOTIFY pgrst, 'reload schema'.", ""]
out += [f"create extension if not exists {e['extname']};" for e in ext] + [""]
out.append("create table if not exists public.knowledge (")
out.append(",\n".join(f"  {c['column_name']} {c['ftype']}" + ("" if c['is_nullable']=='YES' else " not null") + (f" default {c['column_default']}" if c['column_default'] else "") for c in cols))
out.append(");\n")
for i in idx:
    out.append(i["indexdef"].replace("CREATE INDEX", "create index if not exists").replace("CREATE UNIQUE INDEX", "create unique index if not exists") + ";")
out.append("")
if rls and rls[0]["relrowsecurity"]:
    out.append("alter table public.knowledge enable row level security;")
for p in pol:
    out.append(f"-- policy {p['policyname']}: {p['cmd']} for {p['roles']} using ({p['qual']})" + (f" with check ({p['with_check']})" if p['with_check'] else ""))
out.append("")
for f in fns:
    out.append(f["def"].rstrip().rstrip(";") + ";\n")
for t in trg:  # after the functions they call; drop first so a re-run is idempotent
    out.append(f"drop trigger if exists {t['tgname']} on public.knowledge;")
    out.append(t["def"] + ";")
out.append("")
text = "\n".join(out)
text = re.sub(r"(_url\s+constant text := ')[^']+';", r"\1<DISPATCHER_PUBLIC_URL>/api/hooks/knowledge';  -- set per deployment", text)
text = re.sub(r"(_secret constant text := ')[^']+';", r"\1<WEBHOOK_SECRET>';  -- must equal the dispatcher WEBHOOK_SECRET; never commit the real value", text)
assert "<WEBHOOK_SECRET>" in text, "redaction did not match; refusing to write a file with a live secret"
path = f"{H}/scripts/knowledge/sql/knowledge.sql"
open(path, "w").write(text)
print(f"{len(cols)} columns, {len(idx)} indexes, {len(fns)} functions ({', '.join(f['proname']+'('+f['args'][:40]+')' for f in fns)}), {len(pol)} policies, {len(trg)} triggers -> {path}")
