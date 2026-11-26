#!/usr/bin/env python3
"""Runs one SQL script on the local stack (the Pi) and prints the result like `supabase db query`.

The CLI sends local queries as one prepared statement, which cannot hold several statements, but
the smoke checks need `begin; set local role ...; ...; rollback;` in one session. This runs them
over the simple query protocol and prints {"rows": [...]} with the rows of the last statement that
returned rows, or {"_tag": "Error", "error": {...}} when a statement fails, so smoke.sh can parse
both targets the same way. Needs python3-psycopg (sudo apt install python3-psycopg).

    python3 supabase/tests/local_sql.py "<sql>"
"""
import json
import os
import sys

import psycopg


def main():
    sql = sys.argv[1]
    dsn = os.environ.get("LOCAL_DB_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres")
    try:
        with psycopg.connect(dsn, autocommit=True, connect_timeout=10) as conn:
            cur = conn.cursor()
            cur.execute(sql.encode())  # bytes: no parameters, so the simple query protocol is used
            rows = []
            while True:
                if cur.description is not None:
                    names = [column.name for column in cur.description]
                    rows = [dict(zip(names, row)) for row in cur.fetchall()]
                if not cur.nextset():
                    break
    except psycopg.Error as exc:
        diag = exc.diag
        parts = [diag.sqlstate, diag.message_primary, diag.message_detail, diag.message_hint]
        print(json.dumps({"_tag": "Error", "error": {"code": diag.sqlstate,
                                                    "message": " | ".join(p for p in parts if p) or str(exc)}}))
        return 1
    print(json.dumps({"rows": rows}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
