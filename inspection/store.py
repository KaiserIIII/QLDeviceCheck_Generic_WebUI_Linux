import json
import sqlite3
import threading
from pathlib import Path

from .domain import STATUSES, TERMINAL


HISTORY_FIELDS = ('id', 'created_at', 'status', 'mode', 'scenario', 'metadata', 'summary',
                  'progress', 'config_hash', 'parent_job_id', 'started_at', 'finished_at', 'duration_ms')
INDEX_FIELDS = ('projection', 'metadata_search', 'id_search', 'mode', 'summary_verdict',
                'summary_passed', 'summary_expected')


def indexed_values(job):
    """Keep evidence solely in the original record; indexes contain summary data."""
    summary = job['summary']
    return (json.dumps({key: job.get(key) for key in HISTORY_FIELDS}, ensure_ascii=False),
            json.dumps(job['metadata'], ensure_ascii=False).casefold(), job['id'].casefold(),
            job['mode'], summary['verdict'], summary['passed'], summary['expected'])


class SQLiteStore:
    """One process owns this store. Each complete JSON record is committed atomically."""
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.connection = sqlite3.connect(str(path), check_same_thread=False, timeout=5)
        self.connection.execute('PRAGMA journal_mode=WAL')
        self.connection.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, status TEXT NOT NULL, record TEXT NOT NULL)')
        try:
            with self.connection:
                # sqlite3 does not start a transaction for DDL automatically.
                self.connection.execute('BEGIN')
                columns = {row[1] for row in self.connection.execute('PRAGMA table_info(jobs)')}
                for name in INDEX_FIELDS:
                    if name not in columns:
                        kind = 'INTEGER' if name in ('summary_passed', 'summary_expected') else 'TEXT'
                        self.connection.execute('ALTER TABLE jobs ADD COLUMN ' + name + ' ' + kind)
                # A cursor streams one old record at a time. Updating only derived columns
                # preserves record bytes and rowid ordering, and a transaction makes retry safe.
                cursor = self.connection.execute('SELECT rowid, record FROM jobs WHERE projection IS NULL')
                for rowid, raw in cursor:
                    self.connection.execute('UPDATE jobs SET ' + ', '.join(name + '=?' for name in INDEX_FIELDS) + ' WHERE rowid=?',
                                            (*indexed_values(json.loads(raw)), rowid))
                self.connection.execute('CREATE INDEX IF NOT EXISTS jobs_created ON jobs(created_at)')
                self.connection.execute('CREATE INDEX IF NOT EXISTS jobs_status_created ON jobs(status, created_at)')
                self.connection.execute('CREATE INDEX IF NOT EXISTS jobs_mode_status ON jobs(mode, status)')
        except Exception:
            self.connection.close()
            raise

    def insert(self, job):
        with self.lock, self.connection:
            self.connection.execute('INSERT INTO jobs (id, created_at, status, record, ' + ', '.join(INDEX_FIELDS) + ') VALUES (' + ', '.join('?' for _ in range(4 + len(INDEX_FIELDS))) + ')',
                                    (job['id'], job['created_at'], job['status'], json.dumps(job, ensure_ascii=False), *indexed_values(job)))

    def get(self, job_id):
        with self.lock:
            row = self.connection.execute('SELECT record FROM jobs WHERE id=?', (job_id,)).fetchone()
        if row is None:
            raise KeyError('Job not found')
        return json.loads(row[0])

    def update(self, job):
        with self.lock, self.connection:
            cursor = self.connection.execute('UPDATE jobs SET status=?, record=?, ' + ', '.join(name + '=?' for name in INDEX_FIELDS) + ' WHERE id=?',
                                             (job['status'], json.dumps(job, ensure_ascii=False), *indexed_values(job), job['id']))
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
        clauses, params = [], []
        if status:
            clauses.append('status=?')
            params.append(status)
        if query:
            # instr is literal substring search, so % and _ keep their original meaning.
            clauses.append('(instr(metadata_search, ?) > 0 OR instr(id_search, ?) > 0)')
            params.extend((query.casefold(), query.casefold()))
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        with self.lock, self.connection:
            total = self.connection.execute('SELECT count(*) FROM jobs' + where, params).fetchone()[0]
            rows = self.connection.execute('SELECT projection, created_at, status FROM jobs' + where +
                                           ' ORDER BY created_at ' + direction + ', rowid ' + direction + ' LIMIT ? OFFSET ?',
                                           (*params, limit, offset)).fetchall()
        jobs = [dict(json.loads(raw), created_at=created, status=saved_status) for raw, created, saved_status in rows]
        return {'jobs': jobs, 'total': total, 'limit': limit, 'offset': offset, 'sort': sort}

    def unfinished(self):
        """Stream full records only when startup recovery must modify them."""
        with self.lock:
            cursor = self.connection.execute('SELECT record FROM jobs WHERE status NOT IN (' +
                                             ', '.join('?' for _ in TERMINAL) + ')', tuple(TERMINAL))
            for row in cursor:
                yield json.loads(row[0])

    def insights(self):
        output = {mode: dict(total=0, completed=0, passed=0, failed=0, devices_passed=0, devices_expected=0)
                  for mode in ('demo', 'live')}
        with self.lock:
            rows = self.connection.execute("""SELECT mode, count(*),
                sum(status='completed'), sum(status='completed' AND summary_verdict='PASS'),
                sum(status='completed' AND summary_verdict='FAIL'),
                sum(CASE WHEN status='completed' THEN summary_passed ELSE 0 END),
                sum(CASE WHEN status='completed' THEN summary_expected ELSE 0 END)
                FROM jobs GROUP BY mode""").fetchall()
        for mode, *values in rows:
            if mode in output:
                output[mode] = dict(zip(output[mode], values))
        return output

    def all(self):
        with self.lock:
            return [json.loads(row[0]) for row in self.connection.execute('SELECT record FROM jobs').fetchall()]

    def close(self):
        with self.lock:
            self.connection.close()
