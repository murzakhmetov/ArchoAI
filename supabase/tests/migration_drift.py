#!/usr/bin/env python3
"""Drift check: do the migration files still describe the live project?

Check 1 (changed objects). It fingerprints the project's schema (functions with their bodies, grants and
settings, columns, table and column grants, row level security, policies, constraints, indexes, triggers,
realtime membership and the seed rows), re-applies every file in supabase/migrations in name order, and
fingerprints again, all inside ONE transaction that is rolled back. The migrations are idempotent, so an
object that matches its file shows no difference. A difference means somebody changed such an object by
hand (dashboard, SQL editor), or a file was edited after it was applied. Nothing is committed.

Check 2 (unknown objects). Re-applying files cannot remove an object they know nothing about, so check 1
cannot see it. Check 2 lists the policies, functions, tables and triggers of the project and reports each
one whose name appears in no migration file.

Not covered: columns added by hand, auto-named indexes and constraints, and table data.

    python supabase/tests/migration_drift.py                # the server on this Raspberry Pi
    python supabase/tests/migration_drift.py --ref <ref>    # a Supabase cloud project (logged-in CLI)

Exit code 0 = no drift, 1 = drift or error. While it runs, the tables are locked for a moment
(inserts wait, nothing is lost).
"""
import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
MIGRATIONS = HERE.parent / "migrations"

NOT_EXT = "not exists (select 1 from pg_depend d where d.objid = p.oid and d.deptype = 'e')"
GRANTEE = "case when a.grantee = 0 then 'PUBLIC' else a.grantee::regrole::text end"
FINGERPRINT = f"""
select x from (
  select 'fn:' || n.nspname || '.' || p.proname || '(' || pg_get_function_identity_arguments(p.oid) || '):' || md5(p.prosrc) || ':' || p.prosecdef::text || ':' || coalesce(array_to_string(p.proconfig, ','), '') || ':' || p.provolatile::text || ':' || pg_get_function_result(p.oid) as x
    from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname in ('public','private') and {NOT_EXT}
  union all
  select 'facl:' || n.nspname || '.' || p.proname || '(' || pg_get_function_identity_arguments(p.oid) || '):' || {GRANTEE} || ':' || a.privilege_type
    from pg_proc p join pg_namespace n on n.oid = p.pronamespace, lateral aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
    where n.nspname in ('public','private') and {NOT_EXT}
  union all
  select 'col:' || table_name || '.' || column_name || ':' || data_type || ':' || is_nullable || ':' || coalesce(column_default, '') || ':' || is_identity || ':' || coalesce(identity_generation, '')
    from information_schema.columns where table_schema = 'public'
  union all
  select 'tacl:' || c.relname || ':' || {GRANTEE} || ':' || a.privilege_type
    from pg_class c, lateral aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) a where c.relnamespace = 'public'::regnamespace and c.relkind = 'r'
  union all
  select 'cacl:' || c.relname || '.' || at.attname || ':' || {GRANTEE} || ':' || a.privilege_type
    from pg_class c join pg_attribute at on at.attrelid = c.oid and at.attnum > 0 and at.attacl is not null, lateral aclexplode(at.attacl) a
    where c.relnamespace = 'public'::regnamespace and c.relkind = 'r'
  union all
  select 'rls:' || relname || ':' || relrowsecurity::text || ':' || relforcerowsecurity::text from pg_class where relnamespace = 'public'::regnamespace and relkind = 'r'
  union all
  select 'pol:' || schemaname || '.' || tablename || '.' || policyname || ':' || permissive || ':' || cmd || ':' || array_to_string(roles, ',') || ':' || coalesce(qual, '') || ':' || coalesce(with_check, '')
    from pg_policies where schemaname in ('public','storage')
  union all
  select 'con:' || conrelid::regclass::text || ':' || conname || ':' || pg_get_constraintdef(oid) from pg_constraint where connamespace = 'public'::regnamespace
  union all
  select 'idx:' || indexdef from pg_indexes where schemaname = 'public'
  union all
  select 'trg:' || pg_get_triggerdef(t.oid) from pg_trigger t join pg_class c on c.oid = t.tgrelid where not t.tgisinternal and c.relnamespace = 'public'::regnamespace
  union all
  select 'pub:' || schemaname || '.' || tablename from pg_publication_tables where pubname = 'supabase_realtime' and schemaname = 'public'
  union all
  select 'row:model_buckets:' || (to_jsonb(b) - 'created_at' - 'updated_at')::text from public.model_buckets b
  union all
  select 'row:app_limits:' || (to_jsonb(l) - 'created_at' - 'updated_at')::text from public.app_limits l
  union all
  select 'schema:private:' || {GRANTEE} || ':' || a.privilege_type
    from pg_namespace n, lateral aclexplode(coalesce(n.nspacl, acldefault('n', n.nspowner))) a where n.nspname = 'private'
  union all
  select 'schema:private:exists' where exists (select 1 from pg_namespace where nspname = 'private')
) q
"""


INVENTORY = (
    "select json_build_object("
    "'policies', (select coalesce(json_agg(distinct policyname), '[]'::json) from pg_policies "
    "where schemaname = 'public' or (schemaname = 'storage' and tablename = 'objects')), "
    "'functions', (select coalesce(json_agg(distinct p.proname), '[]'::json) from pg_proc p "
    "where p.pronamespace in ('public'::regnamespace, 'private'::regnamespace) and " + NOT_EXT + "), "
    "'tables', (select coalesce(json_agg(c.relname), '[]'::json) from pg_class c "
    "where c.relnamespace = 'public'::regnamespace and c.relkind in ('r', 'p', 'v', 'm')), "
    "'triggers', (select coalesce(json_agg(distinct t.tgname), '[]'::json) from pg_trigger t "
    "join pg_class c on c.oid = t.tgrelid where not t.tgisinternal and c.relnamespace = 'public'::regnamespace)"
    ") as inventory"
)


def query(cli: str, ref: str, sql: str = None, sql_file: pathlib.Path = None):
    """Runs one statement through `supabase db query` and returns the parsed JSON row, or None.

    ``ref`` None means the local stack (the Pi), otherwise the cloud project with that ref."""
    if ref:
        command = [cli, "db", "query", f"--remote={ref}"] + (["-f", str(sql_file)] if sql_file else [sql])
    else:
        # The CLI sends local SQL as one prepared statement, which cannot hold the multi-statement
        # chain; local_sql.py runs it in one session and prints the same JSON shape.
        text = sql_file.read_text(encoding="utf-8") if sql_file else sql
        command = [sys.executable, str(HERE / "local_sql.py"), text]
    done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = done.stdout + done.stderr
    start = out.find("{")
    try:
        doc, _ = json.JSONDecoder().raw_decode(out[start:]) if start >= 0 else (None, 0)
    except json.JSONDecodeError:
        doc = None
    if not doc or "rows" not in doc or not doc["rows"]:
        print("FAIL  the query did not return a result:")
        print(out[:1500])
        return None
    return doc["rows"][0]


def read(path: pathlib.Path) -> str:
    """Reads a UTF-8 file with LF endings; the long-path prefix keeps deep Windows paths working."""
    p = str(path.resolve())
    if os.name == "nt":
        p = chr(92) * 2 + "?" + chr(92) + p
    with open(p, encoding="utf-8", newline="") as handle:
        return handle.read().replace("\r\n", "\n")


# Anything that ends the transaction besides the bare begin; / commit; lines stripped below would make the
# "rolled back" run permanent, so such a file is refused. A plain `end;` is also the end of a plpgsql block and
# cannot be told apart here: the canary table in build_sql() catches that case at run time.
TRANSACTION_CONTROL = re.compile(
    r"^\s*(commit|rollback|abort|savepoint|release|start\s+transaction|begin\s+(transaction|work)|end\s+(transaction|work))\b",
    re.IGNORECASE,
)


def strip_transaction(sql: str, name: str) -> str:
    """Removes the per-file begin / commit / notify lines: the whole chain runs in one transaction."""
    kept = []
    for number, line in enumerate(sql.split("\n"), 1):
        text = line.strip().lower()
        if text in ("begin;", "commit;") or text.startswith("notify pgrst"):
            continue
        if TRANSACTION_CONTROL.match(line):
            raise SystemExit(
                f"FAIL  {name}:{number} has a transaction statement this check cannot neutralise: {line.strip()!r}"
            )
        kept.append(line)
    return "\n".join(kept)


def build_sql(files) -> str:
    chain = "\n".join(f"-- ===== {f.name}\n" + strip_transaction(read(f), f.name) for f in files)
    return f"""begin;
create temp table fp(label text, x text);
create temp table canary(x int) on commit drop;
insert into fp select 'before', x from ({FINGERPRINT}) z;
{chain}
do $canary$
begin
  if to_regclass('pg_temp.canary') is null then
    raise exception 'a migration file committed the transaction: the files were APPLIED to the project, not just checked';
  end if;
end
$canary$;
insert into fp select 'after', x from ({FINGERPRINT}) z;
select json_build_object(
  'counts', (select json_object_agg(label, n) from (select label, count(*) n from fp group by label) c),
  'only_before', (select coalesce(json_agg(x order by x), '[]'::json) from fp a where label = 'before' and not exists (select 1 from fp b where b.label = 'after' and b.x = a.x)),
  'only_after', (select coalesce(json_agg(x order by x), '[]'::json) from fp a where label = 'after' and not exists (select 1 from fp b where b.label = 'before' and b.x = a.x))
) as result;
rollback;
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--ref", default=os.environ.get("REMOTE") or os.environ.get("REF"),
                        help="cloud project ref (default: the local stack on this Pi)")
    args = parser.parse_args()

    cli = shutil.which("supabase")
    if not cli:
        print("FAIL  the supabase CLI is not on PATH")
        return 1
    files = sorted(MIGRATIONS.glob("*.sql"))
    if not files:
        print(f"FAIL  no migrations in {MIGRATIONS}")
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        sql_file = pathlib.Path(tmp) / "drift.sql"
        with open(sql_file, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(build_sql(files))
        print(f"target: {args.ref or 'local stack on this Pi'} | {len(files)} migration files re-applied in one rolled-back transaction")
        row = query(cli, args.ref, sql_file=sql_file)
    if row is None:
        return 1

    result = row["result"]
    before, after = result["only_before"], result["only_after"]
    print("facts compared:", result["counts"])
    drift = False
    if before or after:
        drift = True
        print("FAIL  check 1: a migration file would change the live project")
        for fact in before:
            print("  live only (a file would change or remove it):", fact)
        for fact in after:
            print("  files only (a file would add it):", fact)

    inventory_row = query(cli, args.ref, sql=INVENTORY)
    if inventory_row is None:
        return 1
    text = "\n".join(read(f) for f in files)
    unknown = {
        kind: sorted(name for name in set(names) if name not in text)
        for kind, names in inventory_row["inventory"].items()
    }
    if any(unknown.values()):
        drift = True
        print("FAIL  check 2: objects of the project that no migration file mentions")
        singular = {"policies": "policy", "functions": "function", "tables": "table", "triggers": "trigger"}
        for kind, names in unknown.items():
            for name in names:
                print(f"  {singular.get(kind, kind)}: {name}")
    if drift:
        return 1
    print("PASS  no drift: the migration files describe the live project (changed and unknown objects checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
