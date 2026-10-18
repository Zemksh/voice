import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = root / 'practice.sqlite3'
        with self.connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('''CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, topic TEXT NOT NULL, created_at TEXT NOT NULL,
                status TEXT NOT NULL, job_kind TEXT NOT NULL DEFAULT 'transcribe',
                error TEXT, original_transcript TEXT, corrected_transcript TEXT,
                duration REAL, feedback TEXT, mock INTEGER NOT NULL, media_type TEXT NOT NULL
            )''')

    @contextmanager
    def connection(self):
        with sqlite3.connect(self.path, timeout=10) as db:
            db.row_factory = sqlite3.Row
            yield db

    def get(self, sid):
        with self.connection() as db:
            row = db.execute('SELECT * FROM sessions WHERE id=?', (sid,)).fetchone()
        return self.decode(row) if row else None

    def all(self):
        with self.connection() as db:
            return [self.decode(r) for r in db.execute('SELECT * FROM sessions ORDER BY created_at DESC')]

    @staticmethod
    def decode(row):
        item = dict(row)
        item['mock'] = bool(item['mock'])
        item['feedback'] = json.loads(item['feedback']) if item['feedback'] else None
        return item

    def update(self, sid, **values):
        with self.connection() as db:
            db.execute(f"UPDATE sessions SET {', '.join(k + '=?' for k in values)} WHERE id=?",
                       (*values.values(), sid))

    def interrupt(self):
        with self.connection() as db:
            db.execute("UPDATE sessions SET status='interrupted', error='Processing was interrupted. Retry this step.' "
                       "WHERE status IN ('queued','converting','transcribing','analyzing')")
