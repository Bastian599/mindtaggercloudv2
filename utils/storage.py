# utils/storage.py (dynamic Postgres/SQLite storage with schema resilience)
import os, json, time
from typing import Optional, Dict, Any

DB_URL = os.getenv("DATABASE_URL","").strip()
USE_PG = DB_URL.startswith("postgres://") or DB_URL.startswith("postgresql://")

if USE_PG:
    import psycopg2
    import psycopg2.extras
    from psycopg2 import errors as pg_errors
else:
    import sqlite3

REQUIRED_USER_OAUTH_COLS = {
    "email":      "TEXT",
    "cloud_id":   "TEXT",
    "cloud_url":  "TEXT",
    "cloud_name": "TEXT",
    "token_json": "TEXT",
    "cloud_json": "TEXT",
    "saved_at":   "BIGINT"
}
REQUIRED_UNDO_COLS = {
    "email":      "TEXT",
    "issue_key":  "TEXT",
    "worklog_id": "TEXT",
    "saved_at":   "BIGINT"
}

class Storage:
    def __init__(self, db_url: str = ""):
        self.db_url = db_url or DB_URL
        self.use_pg = self.db_url.startswith("postgres://") or self.db_url.startswith("postgresql://")
        self._conn = self._connect()
        self._init()

    def _connect(self):
        if self.use_pg:
            url = self.db_url
            if "sslmode=" not in url:
                sep = "&" if "?" in url else "?"
                url = f"{url}{sep}sslmode=require"
            conn = psycopg2.connect(url)
            with conn.cursor() as cur:
                cur.execute("SET search_path TO public")
            conn.commit()
            return conn
        else:
            path = "app.db"
            if self.db_url.startswith("sqlite:///"):
                path = self.db_url.replace("sqlite:///","",1)
            return sqlite3.connect(path, check_same_thread=False)

    def _pg_table_columns(self, table: str) -> set:
        cur = self._conn.cursor()
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name=%s
        """, (table,))
        return {r[0] for r in cur.fetchall()}

    def _pg_ensure_columns(self, table: str, cols: Dict[str,str]):
        existing = self._pg_table_columns(table)
        cur = self._conn.cursor()
        for col, ddl in cols.items():
            if col not in existing:
                cur.execute(f'ALTER TABLE public.{table} ADD COLUMN {col} {ddl}')
        self._conn.commit()

    def _init(self):
        if self.use_pg:
            cur = self._conn.cursor()
            cur.execute("CREATE TABLE IF NOT EXISTS public.user_oauth (id SERIAL PRIMARY KEY)")
            cur.execute("CREATE TABLE IF NOT EXISTS public.undo_worklog (id SERIAL PRIMARY KEY)")
            self._conn.commit()
            self._pg_ensure_columns("user_oauth", REQUIRED_USER_OAUTH_COLS)
            self._pg_ensure_columns("undo_worklog", REQUIRED_UNDO_COLS)
            cols = self._pg_table_columns("user_oauth")
            if "site_url" not in cols:
                cur = self._conn.cursor()
                cur.execute("ALTER TABLE public.user_oauth ADD COLUMN IF NOT EXISTS site_url TEXT")
                self._conn.commit()
        else:
            c = self._conn.cursor()
            c.execute("""CREATE TABLE IF NOT EXISTS user_oauth (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT,
                cloud_id TEXT,
                cloud_url TEXT,
                cloud_name TEXT,
                token_json TEXT,
                cloud_json TEXT,
                saved_at INTEGER
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS undo_worklog (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT,
                issue_key TEXT,
                worklog_id TEXT,
                saved_at INTEGER
            )""")
            self._conn.commit()

    def _dynamic_insert(self, table: str, data: Dict[str, Any]):
        if self.use_pg:
            cols = self._pg_table_columns(table)
            preferred = ["email","site_url","cloud_id","cloud_url","cloud_name","token_json","cloud_json","saved_at"]
            insert_cols = [c for c in preferred if c in cols and c in data]
            insert_cols += [c for c in data.keys() if c in cols and c not in insert_cols]
            placeholders = ",".join(["%s"] * len(insert_cols))
            col_list = ",".join(insert_cols)
            values = tuple(data[c] for c in insert_cols)
            sql = f"INSERT INTO public.{table} ({col_list}) VALUES ({placeholders})"
            cur = self._conn.cursor()
            cur.execute(sql, values)
            self._conn.commit()
        else:
            c = self._conn.cursor()
            c.execute(f"PRAGMA table_info({table})")
            cols = {r[1] for r in c.fetchall()}
            insert_cols = [k for k in data.keys() if k in cols]
            placeholders = ",".join(["?"] * len(insert_cols))
            col_list = ",".join(insert_cols)
            values = tuple(data[c] for c in insert_cols)
            sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"
            c.execute(sql, values)
            self._conn.commit()

    def save_oauth(self, email: str, token: dict, cloud: dict):
        ts = int(time.time())
        cloud = cloud or {}
        record = {
            "email":      email or "unknown",
            "cloud_id":   cloud.get("id"),
            "cloud_url":  cloud.get("url"),
            "cloud_name": cloud.get("name"),
            "site_url":   cloud.get("url"),  # für evtl. NOT NULL alte Schemas
            "token_json": json.dumps(token),
            "cloud_json": json.dumps(cloud),
            "saved_at":   ts,
        }
        try:
            self._dynamic_insert("user_oauth", record)
        except Exception as e:
            if self.use_pg and isinstance(e, pg_errors.UndefinedColumn):
                self._init()
                self._dynamic_insert("user_oauth", record)
            else:
                raise

    def update_oauth_token(self, token: dict):
        ts = int(time.time())
        if self.use_pg:
            cur = self._conn.cursor()
            cur.execute(
                """UPDATE public.user_oauth SET token_json=%s, saved_at=%s
                   WHERE id = (SELECT id FROM public.user_oauth ORDER BY id DESC LIMIT 1)""",
                (json.dumps(token), ts)
            )
            self._conn.commit()
        else:
            c = self._conn.cursor()
            c.execute(
                """UPDATE user_oauth SET token_json=?, saved_at=?
                   WHERE id=(SELECT id FROM user_oauth ORDER BY id DESC LIMIT 1)""",
                (json.dumps(token), ts)
            )
            self._conn.commit()

    def set_last_worklog(self, email: str, worklog_id: str, issue_key: str):
        ts = int(time.time())
        if self.use_pg:
            cur = self._conn.cursor()
            cur.execute(
                """INSERT INTO public.undo_worklog(email, issue_key, worklog_id, saved_at)
                   VALUES (%s,%s,%s,%s)""",
                (email, issue_key, worklog_id, ts)
            )
            self._conn.commit()
        else:
            c = self._conn.cursor()
            c.execute(
                """INSERT INTO undo_worklog(email, issue_key, worklog_id, saved_at)
                   VALUES (?,?,?,?)""",
                (email, issue_key, worklog_id, ts)
            )
            self._conn.commit()

    def get_last_worklog(self, email: str) -> Optional[Dict[str,Any]]:
        if self.use_pg:
            cur = self._conn.cursor()
            cur.execute(
                """SELECT issue_key, worklog_id FROM public.undo_worklog
                   WHERE email=%s ORDER BY id DESC LIMIT 1""",
                (email,)
            )
            row = cur.fetchone()
        else:
            c = self._conn.cursor()
            c.execute(
                """SELECT issue_key, worklog_id FROM undo_worklog
                   WHERE email=? ORDER BY id DESC LIMIT 1""",
                (email,)
            )
            row = c.fetchone()
        if not row:
            return None
        return {"issue_key": row[0], "worklog_id": row[1]}

    def clear_last_worklog(self, email: str):
        if self.use_pg:
            cur = self._conn.cursor()
            cur.execute(
                """DELETE FROM public.undo_worklog WHERE id IN (
                       SELECT id FROM public.undo_worklog WHERE email=%s
                       ORDER BY id DESC LIMIT 1
                   )""",
                (email,)
            )
            self._conn.commit()
        else:
            c = self._conn.cursor()
            c.execute(
                """DELETE FROM undo_worklog WHERE id IN (
                       SELECT id FROM undo_worklog WHERE email=?
                       ORDER BY id DESC LIMIT 1
                   )""",
                (email,)
            )
            self._conn.commit()

    def ping(self) -> Dict[str,Any]:
        try:
            if self.use_pg:
                cur = self._conn.cursor()
                cur.execute("SHOW search_path")
                sp = cur.fetchone()[0]
                cur.execute("SELECT version()")
                ver = cur.fetchone()[0]
                return {"ok": True, "driver": "psycopg2", "version": ver, "search_path": sp}
            else:
                c = self._conn.cursor()
                c.execute("SELECT sqlite_version()")
                ver = c.fetchone()[0]
                return {"ok": True, "driver": "sqlite3", "version": ver}
        except Exception as e:
            return {"ok": False, "error": str(e)}
