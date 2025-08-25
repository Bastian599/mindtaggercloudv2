# utils/storage.py
import os, json, time
from typing import Optional, Dict, Any

DB_URL = os.getenv("DATABASE_URL","").strip()

# Decide backend: Postgres (Neon) or SQLite
USE_PG = DB_URL.startswith("postgres://") or DB_URL.startswith("postgresql://")

if USE_PG:
    import psycopg2
    import psycopg2.extras
else:
    import sqlite3

class Storage:
    def __init__(self, db_url: str = ""):
        self.db_url = db_url or DB_URL
        self.use_pg = self.db_url.startswith("postgres://") or self.db_url.startswith("postgresql://")
        self._conn = self._connect()
        self._init()

    def _connect(self):
        if self.use_pg:
            # Neon requires SSL; ensure sslmode=require in URL if not present
            url = self.db_url
            if "sslmode=" not in url:
                sep = "&" if "?" in url else "?"
                url = f"{url}{sep}sslmode=require"
            return psycopg2.connect(url)
        else:
            path = "app.db"
            if self.db_url.startswith("sqlite:///"):
                path = self.db_url.replace("sqlite:///","",1)
            return sqlite3.connect(path, check_same_thread=False)

    def _init(self):
        if self.use_pg:
            cur = self._conn.cursor()
            cur.execute("""CREATE TABLE IF NOT EXISTS user_oauth (
                id SERIAL PRIMARY KEY,
                email TEXT,
                token_json TEXT,
                cloud_json TEXT,
                saved_at BIGINT
            )""")
            cur.execute("""CREATE TABLE IF NOT EXISTS undo_worklog (
                id SERIAL PRIMARY KEY,
                email TEXT,
                issue_key TEXT,
                worklog_id TEXT,
                saved_at BIGINT
            )""")
            self._conn.commit()
        else:
            c = self._conn.cursor()
            c.execute("""CREATE TABLE IF NOT EXISTS user_oauth (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT,
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

    # ------ OAuth persistence ------
    def save_oauth(self, email: str, token: dict, cloud: dict):
        ts = int(time.time())
        if self.use_pg:
            cur = self._conn.cursor()
            cur.execute("INSERT INTO user_oauth(email, token_json, cloud_json, saved_at) VALUES (%s,%s,%s,%s)",
                        (email, json.dumps(token), json.dumps(cloud), ts))
            self._conn.commit()
        else:
            c = self._conn.cursor()
            c.execute("INSERT INTO user_oauth(email, token_json, cloud_json, saved_at) VALUES (?,?,?,?)",
                      (email, json.dumps(token), json.dumps(cloud), ts))
            self._conn.commit()

    def update_oauth_token(self, token: dict):
        ts = int(time.time())
        if self.use_pg:
            cur = self._conn.cursor()
            # Update latest row
            cur.execute("""UPDATE user_oauth SET token_json=%s, saved_at=%s
                           WHERE id = (SELECT id FROM user_oauth ORDER BY id DESC LIMIT 1)""",
                        (json.dumps(token), ts))
            self._conn.commit()
        else:
            c = self._conn.cursor()
            c.execute("""UPDATE user_oauth SET token_json=?, saved_at=?
                         WHERE id=(SELECT id FROM user_oauth ORDER BY id DESC LIMIT 1)""",
                      (json.dumps(token), ts))
            self._conn.commit()

    # ------ Undo storage ------
    def set_last_worklog(self, email: str, worklog_id: str, issue_key: str):
        ts = int(time.time())
        if self.use_pg:
            cur = self._conn.cursor()
            cur.execute("""INSERT INTO undo_worklog(email, issue_key, worklog_id, saved_at)
                           VALUES (%s,%s,%s,%s)""", (email, issue_key, worklog_id, ts))
            self._conn.commit()
        else:
            c = self._conn.cursor()
            c.execute("""INSERT INTO undo_worklog(email, issue_key, worklog_id, saved_at)
                         VALUES (?,?,?,?)""", (email, issue_key, worklog_id, ts))
            self._conn.commit()

    def get_last_worklog(self, email: str) -> Optional[Dict[str,Any]]:
        if self.use_pg:
            cur = self._conn.cursor()
            cur.execute("""SELECT issue_key, worklog_id FROM undo_worklog
                           WHERE email=%s ORDER BY id DESC LIMIT 1""", (email,))
            row = cur.fetchone()
        else:
            c = self._conn.cursor()
            c.execute("""SELECT issue_key, worklog_id FROM undo_worklog
                         WHERE email=? ORDER BY id DESC LIMIT 1""", (email,))
            row = c.fetchone()
        if not row:
            return None
        return {"issue_key": row[0], "worklog_id": row[1]}

    def clear_last_worklog(self, email: str):
        if self.use_pg:
            cur = self._conn.cursor()
            cur.execute("""DELETE FROM undo_worklog WHERE id IN (
                               SELECT id FROM undo_worklog WHERE email=%s ORDER BY id DESC LIMIT 1
                           )""", (email,))
            self._conn.commit()
        else:
            c = self._conn.cursor()
            c.execute("""DELETE FROM undo_worklog WHERE id IN (
                            SELECT id FROM undo_worklog WHERE email=? ORDER BY id DESC LIMIT 1
                        )""", (email,))
            self._conn.commit()

    # ------ Health ------
    def ping(self) -> Dict[str,Any]:
        try:
            if self.use_pg:
                cur = self._conn.cursor()
                cur.execute("SELECT version()")
                ver = cur.fetchone()[0]
                return {"ok": True, "driver": "psycopg2", "version": ver}
            else:
                c = self._conn.cursor()
                c.execute("SELECT sqlite_version()")
                ver = c.fetchone()[0]
                return {"ok": True, "driver": "sqlite3", "version": ver}
        except Exception as e:
            return {"ok": False, "error": str(e)}
