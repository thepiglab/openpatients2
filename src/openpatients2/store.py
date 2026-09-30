from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from .data import input_fingerprint


class Store:
    """One process/event-loop owns writes. Do not share this state DB across jobs.

    DELETE journal mode avoids WAL shared-memory requirements on cluster filesystems.
    Node-local storage with explicit checkpoint copies is preferable at high scale.
    """
    def __init__(self, path: str | Path, commit_every: int = 64):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=60)
        self.commit_every = commit_every
        self.pending = 0
        self.last_commit = time.monotonic()
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS records(
            record_id TEXT PRIMARY KEY, source_hash TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS record_versions(
            record_id TEXT NOT NULL, input_hash TEXT NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY(record_id,input_hash));
        CREATE TABLE IF NOT EXISTS results(
            record_id TEXT NOT NULL, task TEXT NOT NULL, signature TEXT NOT NULL, input_hash TEXT NOT NULL,
            status TEXT NOT NULL, data TEXT, checks TEXT NOT NULL, raw_content TEXT, generation TEXT,
            PRIMARY KEY(record_id,task,signature,input_hash));
        CREATE TABLE IF NOT EXISTS attempts(
            attempt_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
            record_id TEXT NOT NULL, task TEXT NOT NULL, endpoint TEXT NOT NULL,
            metrics TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            generation TEXT, request TEXT, input_hash TEXT, signature TEXT);
        CREATE INDEX IF NOT EXISTS attempts_run ON attempts(run_id);
        """)
        self.db.commit()
        Path(path).chmod(0o600)

    def flush(self):
        self.db.commit()
        self.pending = 0
        self.last_commit = time.monotonic()

    def changed(self):
        self.pending += 1
        if self.pending >= self.commit_every or time.monotonic() - self.last_commit >= 1:
            self.flush()

    def close(self):
        self.flush()
        self.db.close()

    def put_record(self, record: dict):
        self.db.execute("INSERT OR REPLACE INTO records VALUES(?,?,?)",
                        (record["record_id"], record["source_hash"], json.dumps(record, ensure_ascii=False)))
        self.db.execute("INSERT OR IGNORE INTO record_versions VALUES(?,?,?)",
                        (record["record_id"], input_fingerprint(record), json.dumps(record, ensure_ascii=False)))
        self.changed()

    def get(self, record: dict, task: str, signature: str) -> dict | None:
        row = self.db.execute("SELECT status,data,checks FROM results WHERE record_id=? AND task=? AND signature=? AND input_hash=?",
                              (record["record_id"], task, signature, input_fingerprint(record))).fetchone()
        return {"status": row[0], "data": json.loads(row[1]) if row[1] else None, "checks": json.loads(row[2])} if row else None

    def save(self, record: dict, task: str, signature: str, status: str, data: dict | None, checks: dict, raw: str | None, generation: dict | None = None):
        self.db.execute("INSERT OR REPLACE INTO results(record_id,task,signature,input_hash,status,data,checks,raw_content,generation) VALUES(?,?,?,?,?,?,?,?,?)", (
            record["record_id"], task, signature, input_fingerprint(record), status,
            json.dumps(data, ensure_ascii=False) if data is not None else None,
            json.dumps(checks, ensure_ascii=False), raw, json.dumps(generation, ensure_ascii=False) if generation is not None else None))
        self.changed()

    def attempt(self, run_id: str, record: dict, task: str, endpoint: str, metrics: dict,
                generation: dict | None = None, request: dict | None = None, signature: str | None = None) -> int:
        cursor = self.db.execute(
            "INSERT INTO attempts(run_id,record_id,task,endpoint,metrics,generation,request,input_hash,signature) VALUES(?,?,?,?,?,?,?,?,?)",
            (run_id, record["record_id"], task, endpoint, json.dumps(metrics),
             json.dumps(generation, ensure_ascii=False) if generation is not None else None,
             json.dumps(request, ensure_ascii=False) if request is not None else None,
             input_fingerprint(record), signature))
        self.changed()
        return cursor.lastrowid

    def metrics(self, run_id: str) -> list[dict]:
        return [json.loads(row[0]) for row in self.db.execute("SELECT metrics FROM attempts WHERE run_id=? ORDER BY attempt_id", (run_id,))]
