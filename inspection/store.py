import json
import sqlite3
import threading
from pathlib import Path

from .domain import STATUSES


class SQLiteStore:
    """One process owns this store. Each complete JSON record is committed atomically."""
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.connection = sqlite3.connect(str(path), check_same_thread=False, timeout=5)
        self.connection.execute('PRAGMA journal_mode=WAL')
        self.connection.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, status TEXT NOT NULL, record TEXT NOT NULL)')
        self.connection.commit()

    def insert(self, job):
        with self.lock, self.connection:
            self.connection.execute('INSERT INTO jobs VALUES (?, ?, ?, ?)', (job['id'], job['created_at'], job['status'], json.dumps(job, ensure_ascii=False)))

    def get(self, job_id):
        with self.lock:
            row = self.connection.execute('SELECT record FROM jobs WHERE id=?', (job_id,)).fetchone()
        if row is None:
            raise KeyError('Job not found')
        return json.loads(row[0])

    def update(self, job):
        with self.lock, self.connection:
            cursor = self.connection.execute('UPDATE jobs SET status=?, record=? WHERE id=?', (job['status'], json.dumps(job, ensure_ascii=False), job['id']))
            if not cursor.rowcount:
                raise KeyError('Job not found')

    def list(self, limit=20, offset=0, status='', query='', sort='newest'):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100 or not isinstance(offset, int) or not 0 <= offset <= 1000000:
            raise ValueError('Invalid pagination')
        if status and status not in STATUSES:
            raise ValueError('Invalid status')
        if not isinstance(query, str) or len(query) > 128:
            raise ValueError('Invalid query')
        if sort not in ('newest', 'oldest'):
            raise ValueError('Invalid sort')
        direction = 'DESC' if sort == 'newest' else 'ASC'
        with self.lock:
            rows = self.connection.execute('SELECT record FROM jobs ORDER BY created_at ' + direction + ', rowid ' + direction).fetchall()
        jobs = [json.loads(row[0]) for row in rows]
        jobs = [job for job in jobs if (not status or job['status'] == status) and (not query or query.casefold() in json.dumps(job['metadata'], ensure_ascii=False).casefold() or query.casefold() in job['id'].casefold())]
        return {'jobs': jobs[offset:offset + limit], 'total': len(jobs), 'limit': limit, 'offset': offset, 'sort': sort}

    def all(self):
        with self.lock:
            return [json.loads(row[0]) for row in self.connection.execute('SELECT record FROM jobs').fetchall()]

    def close(self):
        with self.lock:
            self.connection.close()
