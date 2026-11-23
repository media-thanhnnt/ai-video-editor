"""Local single-process background jobs. Not a distributed worker or provider quota oracle."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Lock
import uuid
from typing import Callable


class JobManager:
    def __init__(self, workers: int = 2):
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='ave-ui')
        self._lock = Lock()
        self._jobs: dict[str, dict] = {}

    def submit(self, project_id: str, kind: str, action: Callable[[Callable[[str], None]], dict]):
        with self._lock:
            for previous in self._jobs.values():
                if previous['project_id'] == project_id and previous['status'] in ('queued', 'running'):
                    raise RuntimeError('Project already has an active job; wait for it to finish')
            jid = uuid.uuid4().hex
            self._jobs[jid] = {'job_id': jid, 'project_id': project_id, 'kind': kind,
                               'status': 'queued', 'message': 'Queued', 'percent': 0 if kind == 'render' else None, 'result': None,
                               'error': None, 'created_at': datetime.now(timezone.utc).isoformat()}

        def progress(message: str, percent: int | None = None):
            with self._lock:
                self._jobs[jid]['message'] = message[:600]
                if percent is not None and kind == 'render':
                    current = self._jobs[jid]['percent'] or 0
                    self._jobs[jid]['percent'] = max(current, min(99, max(0, int(percent))))

        def work():
            with self._lock:
                self._jobs[jid]['status'] = 'running'
            try:
                outcome = action(progress)
                with self._lock:
                    self._jobs[jid].update(status='succeeded', message='Completed',
                                           percent=100 if kind == 'render' else None, result=outcome)
            except Exception as exc:
                with self._lock:
                    self._jobs[jid].update(status='failed', message='Failed', error=str(exc)[:1200])

        self._pool.submit(work)
        return self.get(project_id, jid)

    def get(self, project_id: str, job_id: str):
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job and job['project_id'] == project_id else None

    def active(self, project_id: str):
        with self._lock:
            return next((dict(j) for j in self._jobs.values() if j['project_id'] == project_id
                         and j['status'] in ('queued', 'running')), None)
