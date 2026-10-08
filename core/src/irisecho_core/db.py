# SPDX-License-Identifier: AGPL-3.0-or-later
"""Job history in SQLite."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id        TEXT PRIMARY KEY,
    model     TEXT NOT NULL,
    kind      TEXT NOT NULL,
    params    TEXT NOT NULL,
    status    TEXT NOT NULL,
    progress  REAL,
    message   TEXT,
    error     TEXT,
    created   REAL NOT NULL,
    started   REAL,
    finished  REAL,
    outputs   TEXT NOT NULL DEFAULT '[]',
    favorite  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS jobs_created ON jobs(created DESC);
"""

FTS_SCHEMA = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS jobs_fts "
    "USING fts5(id UNINDEXED, text, tokenize='porter unicode61')"
)
# The parts of a job's settings that people remember it by.
SEARCH_KEYS = ("prompt", "text", "lyrics", "caption", "tags", "id")

JSON_COLUMNS = ("params", "outputs")
# Working jobs that are not things the person made: kept briefly, never listed.
HIDDEN_KINDS = ("prompt", "views3d")


def search_text(model: str, params: dict) -> str:
    """What a search looks at: the words in a job's settings, and its model's name."""
    words = [str(params[k]) for k in SEARCH_KEYS if isinstance(params.get(k), str | int | float)]
    return " ".join([model.replace("-", " "), *words])


def fts_query(text: str) -> str:
    """Turn what someone typed into a safe FTS5 query: every word, the last as a prefix."""
    words = re.findall(r"\w+", text, flags=re.UNICODE)
    if not words:
        return ""
    quoted = [f'"{w}"' for w in words]
    quoted[-1] += "*"
    return " ".join(quoted)


class Database:
    def __init__(self, path: Path):
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.conn.executescript("PRAGMA journal_mode=WAL;" + SCHEMA)
            try:
                self.conn.execute(FTS_SCHEMA)
                self.fts = True
            except sqlite3.OperationalError:
                self.fts = False  # this SQLite has no FTS5: search falls back to LIKE
            if self.fts:
                self._backfill()

    def _backfill(self) -> None:
        """Index jobs made before search existed, or whose index rows went missing."""
        rows = self.conn.execute(
            "SELECT id, model, params FROM jobs WHERE id NOT IN (SELECT id FROM jobs_fts)"
        ).fetchall()
        for r in rows:
            self.conn.execute(
                "INSERT INTO jobs_fts (id, text) VALUES (?, ?)",
                (r["id"], search_text(r["model"], json.loads(r["params"]))),
            )

    def _index(self, job_id: str, model: str, params: dict) -> None:
        if not self.fts:
            return
        self.conn.execute("DELETE FROM jobs_fts WHERE id = ?", (job_id,))
        self.conn.execute(
            "INSERT INTO jobs_fts (id, text) VALUES (?, ?)", (job_id, search_text(model, params))
        )

    def _row(self, row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        data = dict(row)
        for col in JSON_COLUMNS:
            data[col] = json.loads(data[col])
        data["favorite"] = bool(data["favorite"])
        return data

    def insert(self, job: dict) -> None:
        cols = list(job)
        values = [json.dumps(job[c]) if c in JSON_COLUMNS else job[c] for c in cols]
        with self.lock:
            self.conn.execute(
                f"INSERT INTO jobs ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                values,
            )
            self._index(job["id"], job["model"], job["params"])

    def update(self, job_id: str, **fields) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        values = [json.dumps(v) if k in JSON_COLUMNS else v for k, v in fields.items()]
        with self.lock:
            self.conn.execute(f"UPDATE jobs SET {sets} WHERE id = ?", [*values, job_id])
            if "params" in fields:
                row = self.conn.execute("SELECT model FROM jobs WHERE id = ?", (job_id,)).fetchone()
                if row:
                    self._index(job_id, row["model"], fields["params"])

    def get(self, job_id: str) -> dict | None:
        with self.lock:
            row = self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._row(row)

    def list(
        self,
        kind: str | None = None,
        status: str | None = None,
        favorite: bool | None = None,
        limit: int = 100,
        before: float | None = None,
        after: float | None = None,
        model: str | None = None,
        q: str | None = None,
    ) -> list[dict]:
        where, args = [], []
        order = "created DESC"
        match = fts_query(q or "")
        if q and q.strip() and not match:
            return []  # only punctuation: nothing can match
        if match and self.fts:
            where.append("id IN (SELECT id FROM jobs_fts WHERE jobs_fts MATCH ?)")
            args.append(match)
        elif match:
            for word in re.findall(r"\w+", q or "", flags=re.UNICODE):
                where.append("(params LIKE ? OR model LIKE ?)")
                args.extend([f"%{word}%"] * 2)
        if model:
            where.append("model = ?")
            args.append(model)
        if after:
            where.append("created >= ?")
            args.append(after)
        if kind:
            where.append("kind = ?")
            args.append(kind)
        else:
            where.append(f"kind NOT IN ({', '.join('?' * len(HIDDEN_KINDS))})")
            args.extend(HIDDEN_KINDS)
        if status:
            where.append("status = ?")
            args.append(status)
        if favorite is not None:
            where.append("favorite = ?")
            args.append(int(favorite))
        if before:
            where.append("created < ?")
            args.append(before)
        sql = "SELECT * FROM jobs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" ORDER BY {order} LIMIT ?"
        with self.lock:
            rows = self.conn.execute(sql, [*args, limit]).fetchall()
        return [self._row(r) for r in rows]

    def queued(self) -> list[dict]:
        """Every job still waiting its turn, oldest first, working kinds included."""
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM jobs WHERE status = 'queued' ORDER BY created"
            ).fetchall()
        return [self._row(r) for r in rows]

    def prune(self, kind: str, keep: int) -> list[str]:
        """Drop all but the newest `keep` jobs of a working kind. Returns the removed ids."""
        with self.lock:
            rows = self.conn.execute(
                "SELECT id FROM jobs WHERE kind = ? AND status NOT IN ('queued', 'running') "
                "ORDER BY created DESC LIMIT -1 OFFSET ?",
                (kind, keep),
            ).fetchall()
            ids = [r["id"] for r in rows]
            for job_id in ids:
                self.conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
                if self.fts:
                    self.conn.execute("DELETE FROM jobs_fts WHERE id = ?", (job_id,))
        return ids

    def delete(self, job_id: str) -> None:
        with self.lock:
            self.conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            if self.fts:
                self.conn.execute("DELETE FROM jobs_fts WHERE id = ?", (job_id,))

    def interrupted(self) -> None:
        """Jobs left queued or running by a crash or a quit are marked as such."""
        with self.lock:
            self.conn.execute(
                "UPDATE jobs SET status = 'interrupted', message = NULL "
                "WHERE status IN ('queued', 'running')"
            )
