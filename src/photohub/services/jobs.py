from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy import delete

from ..models import JobEvent, JobQueue


JOB_STATUS_QUEUED = "queued"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_RETRY_WAITING = "retry_waiting"
JOB_STATUS_COMPLETED = "completed"
JOB_STATUS_FAILED = "failed"
JOB_STATUS_CANCELED = "canceled"


@dataclass(frozen=True)
class JobSnapshot:
    id: int
    job_type: str
    project_id: int | None
    payload: dict
    status: str
    priority: int
    attempts: int
    max_attempts: int
    next_run_at: datetime
    locked_by: str | None
    locked_at: datetime | None
    heartbeat_at: datetime | None
    progress_done: int
    progress_total: int
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class JobQueueService:
    def __init__(
        self,
        session_factory,
        *,
        base_backoff_seconds: int = 5,
        max_backoff_seconds: int = 300,
    ):
        self.session_factory = session_factory
        self.base_backoff_seconds = max(1, int(base_backoff_seconds))
        self.max_backoff_seconds = max(self.base_backoff_seconds, int(max_backoff_seconds))

    def enqueue(
        self,
        *,
        job_type: str,
        payload: dict,
        project_id: int | None = None,
        priority: int = 100,
        max_attempts: int = 3,
        run_at: datetime | None = None,
    ) -> JobSnapshot:
        clean_job_type = str(job_type or "").strip().lower()
        if not clean_job_type:
            raise ValueError("job_type requis.")
        now = datetime.utcnow()
        with self.session_factory() as session:
            model = JobQueue(
                job_type=clean_job_type,
                project_id=project_id,
                payload_json=json.dumps(payload or {}, ensure_ascii=True, separators=(",", ":")),
                status=JOB_STATUS_QUEUED,
                priority=int(priority),
                attempts=0,
                max_attempts=max(1, int(max_attempts)),
                next_run_at=run_at or now,
                created_at=now,
                updated_at=now,
            )
            session.add(model)
            session.flush()
            self._append_event(session, model.id, "info", "Job enfile.")
            session.commit()
            session.refresh(model)
            return self._to_snapshot(model)

    def list_jobs(self, *, statuses: tuple[str, ...] | None = None, limit: int = 200) -> list[JobSnapshot]:
        with self.session_factory() as session:
            query = select(JobQueue).order_by(JobQueue.created_at.desc(), JobQueue.id.desc())
            if statuses:
                query = query.where(JobQueue.status.in_([str(s) for s in statuses]))
            models = list(session.scalars(query.limit(max(1, int(limit)))).all())
            return [self._to_snapshot(model) for model in models]

    def list_job_events(self, job_id: int, limit: int = 200) -> list[tuple[str, str, datetime]]:
        with self.session_factory() as session:
            rows = list(
                session.scalars(
                    select(JobEvent)
                    .where(JobEvent.job_id == int(job_id))
                    .order_by(JobEvent.created_at.asc(), JobEvent.id.asc())
                    .limit(max(1, int(limit)))
                ).all()
            )
            return [(str(row.level), str(row.message), row.created_at) for row in rows]

    def get_job(self, job_id: int) -> JobSnapshot | None:
        with self.session_factory() as session:
            model = session.get(JobQueue, int(job_id))
            if model is None:
                return None
            return self._to_snapshot(model)

    def recover_stale_running_jobs(self, stale_after_seconds: int = 90) -> int:
        now = datetime.utcnow()
        stale_before = now - timedelta(seconds=max(5, int(stale_after_seconds)))
        recovered = 0
        with self.session_factory() as session:
            jobs = list(
                session.scalars(
                    select(JobQueue).where(
                        and_(
                            JobQueue.status == JOB_STATUS_RUNNING,
                            or_(JobQueue.heartbeat_at.is_(None), JobQueue.heartbeat_at < stale_before),
                        )
                    )
                ).all()
            )
            for model in jobs:
                if int(model.attempts) < int(model.max_attempts):
                    delay = self._compute_retry_delay_seconds(int(model.attempts))
                    model.status = JOB_STATUS_RETRY_WAITING
                    model.next_run_at = now + timedelta(seconds=delay)
                    model.error_code = "stale_recovered"
                    model.error_message = "Job recupere apres interruption."
                    self._append_event(
                        session,
                        model.id,
                        "warning",
                        f"Job stale recupere: retry dans {delay}s.",
                    )
                else:
                    model.status = JOB_STATUS_FAILED
                    model.error_code = "stale_exhausted"
                    model.error_message = "Job stale sans tentative restante."
                    self._append_event(session, model.id, "error", "Job stale marque failed.")
                model.locked_by = None
                model.locked_at = None
                model.heartbeat_at = None
                model.updated_at = now
                recovered += 1
            session.commit()
        return recovered

    def claim_next(self, *, worker_id: str, allowed_job_types: tuple[str, ...] | None = None) -> JobSnapshot | None:
        now = datetime.utcnow()
        with self.session_factory() as session:
            query = select(JobQueue).where(
                and_(
                    JobQueue.status.in_([JOB_STATUS_QUEUED, JOB_STATUS_RETRY_WAITING]),
                    JobQueue.next_run_at <= now,
                )
            )
            if allowed_job_types:
                query = query.where(JobQueue.job_type.in_([str(v).strip().lower() for v in allowed_job_types]))
            model = session.scalar(query.order_by(JobQueue.priority.asc(), JobQueue.created_at.asc(), JobQueue.id.asc()))
            if model is None:
                return None
            return self._claim_model(session, model, worker_id=str(worker_id), now=now)

    def claim_job(self, *, job_id: int, worker_id: str) -> JobSnapshot | None:
        now = datetime.utcnow()
        with self.session_factory() as session:
            model = session.get(JobQueue, int(job_id))
            if model is None:
                return None
            if model.status not in {JOB_STATUS_QUEUED, JOB_STATUS_RETRY_WAITING}:
                return None
            if model.next_run_at > now:
                return None
            return self._claim_model(session, model, worker_id=str(worker_id), now=now)

    def heartbeat(
        self,
        *,
        job_id: int,
        worker_id: str,
        progress_done: int | None = None,
        progress_total: int | None = None,
        message: str | None = None,
    ) -> None:
        now = datetime.utcnow()
        with self.session_factory() as session:
            model = session.get(JobQueue, int(job_id))
            if model is None:
                raise ValueError("Job introuvable.")
            self._ensure_lock_owner(model, worker_id)
            if model.status != JOB_STATUS_RUNNING:
                return
            model.heartbeat_at = now
            if progress_done is not None:
                model.progress_done = max(0, int(progress_done))
            if progress_total is not None:
                model.progress_total = max(0, int(progress_total))
            model.updated_at = now
            if message:
                self._append_event(session, model.id, "debug", str(message))
            session.commit()

    def complete(self, *, job_id: int, worker_id: str, message: str = "") -> JobSnapshot:
        now = datetime.utcnow()
        with self.session_factory() as session:
            model = session.get(JobQueue, int(job_id))
            if model is None:
                raise ValueError("Job introuvable.")
            self._ensure_lock_owner(model, worker_id)
            model.status = JOB_STATUS_COMPLETED
            model.locked_by = None
            model.locked_at = None
            model.heartbeat_at = now
            model.next_run_at = now
            model.updated_at = now
            model.error_code = None
            model.error_message = None
            if message:
                self._append_event(session, model.id, "info", str(message))
            self._append_event(session, model.id, "info", "Job termine.")
            session.commit()
            session.refresh(model)
            return self._to_snapshot(model)

    def fail(
        self,
        *,
        job_id: int,
        worker_id: str,
        error_message: str,
        error_code: str = "runtime_error",
    ) -> JobSnapshot:
        now = datetime.utcnow()
        with self.session_factory() as session:
            model = session.get(JobQueue, int(job_id))
            if model is None:
                raise ValueError("Job introuvable.")
            self._ensure_lock_owner(model, worker_id)
            model.error_code = str(error_code or "runtime_error")
            model.error_message = str(error_message or "Erreur job.")

            if int(model.attempts) < int(model.max_attempts):
                delay = self._compute_retry_delay_seconds(int(model.attempts))
                model.status = JOB_STATUS_RETRY_WAITING
                model.next_run_at = now + timedelta(seconds=delay)
                self._append_event(
                    session,
                    model.id,
                    "warning",
                    f"Job en retry ({model.attempts}/{model.max_attempts}) dans {delay}s: {model.error_message}",
                )
            else:
                model.status = JOB_STATUS_FAILED
                model.next_run_at = now
                self._append_event(
                    session,
                    model.id,
                    "error",
                    f"Job failed definitif ({model.attempts}/{model.max_attempts}): {model.error_message}",
                )

            model.locked_by = None
            model.locked_at = None
            model.heartbeat_at = now
            model.updated_at = now
            session.commit()
            session.refresh(model)
            return self._to_snapshot(model)

    def cancel(self, *, job_id: int, reason: str = "") -> JobSnapshot:
        now = datetime.utcnow()
        with self.session_factory() as session:
            model = session.get(JobQueue, int(job_id))
            if model is None:
                raise ValueError("Job introuvable.")
            if model.status in {JOB_STATUS_COMPLETED, JOB_STATUS_FAILED, JOB_STATUS_CANCELED}:
                return self._to_snapshot(model)
            model.status = JOB_STATUS_CANCELED
            model.locked_by = None
            model.locked_at = None
            model.heartbeat_at = now
            model.updated_at = now
            model.error_code = "canceled"
            model.error_message = reason or "Annule par utilisateur."
            self._append_event(session, model.id, "warning", f"Job annule: {model.error_message}")
            session.commit()
            session.refresh(model)
            return self._to_snapshot(model)

    def counts(self) -> dict[str, int]:
        with self.session_factory() as session:
            rows = list(
                session.execute(
                    select(JobQueue.status, func.count(JobQueue.id)).group_by(JobQueue.status)
                ).all()
            )
        result = {
            JOB_STATUS_QUEUED: 0,
            JOB_STATUS_RUNNING: 0,
            JOB_STATUS_RETRY_WAITING: 0,
            JOB_STATUS_COMPLETED: 0,
            JOB_STATUS_FAILED: 0,
            JOB_STATUS_CANCELED: 0,
        }
        for status, count in rows:
            result[str(status)] = int(count)
        return result

    def purge_jobs(self, *, statuses: tuple[str, ...], older_than_seconds: int = 0) -> int:
        if not statuses:
            return 0
        now = datetime.utcnow()
        cutoff = now - timedelta(seconds=max(0, int(older_than_seconds)))
        with self.session_factory() as session:
            jobs = list(
                session.scalars(
                    select(JobQueue.id).where(
                        and_(
                            JobQueue.status.in_([str(s) for s in statuses]),
                            JobQueue.updated_at <= cutoff,
                        )
                    )
                ).all()
            )
            if not jobs:
                return 0
            session.execute(delete(JobEvent).where(JobEvent.job_id.in_(jobs)))
            session.execute(delete(JobQueue).where(JobQueue.id.in_(jobs)))
            session.commit()
            return len(jobs)

    def _append_event(self, session, job_id: int, level: str, message: str) -> None:
        session.add(
            JobEvent(
                job_id=int(job_id),
                level=str(level),
                message=str(message),
                created_at=datetime.utcnow(),
            )
        )

    @staticmethod
    def _ensure_lock_owner(model: JobQueue, worker_id: str) -> None:
        if model.locked_by is None:
            return
        if str(model.locked_by) != str(worker_id):
            raise ValueError("Job locke par un autre worker.")

    def _compute_retry_delay_seconds(self, attempts: int) -> int:
        # attempts starts at 1 on first claim.
        exp = max(0, int(attempts) - 1)
        delay = self.base_backoff_seconds * (2**exp)
        return int(min(self.max_backoff_seconds, delay))

    def _claim_model(self, session, model: JobQueue, *, worker_id: str, now: datetime) -> JobSnapshot:
        model.status = JOB_STATUS_RUNNING
        model.locked_by = str(worker_id)
        model.locked_at = now
        model.heartbeat_at = now
        model.attempts = int(model.attempts) + 1
        model.updated_at = now
        self._append_event(session, model.id, "info", f"Job claim par {worker_id} (try {model.attempts}).")
        session.commit()
        session.refresh(model)
        return self._to_snapshot(model)

    @staticmethod
    def _to_snapshot(model: JobQueue) -> JobSnapshot:
        payload = {}
        try:
            payload = json.loads(model.payload_json or "{}")
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        return JobSnapshot(
            id=int(model.id),
            job_type=str(model.job_type),
            project_id=int(model.project_id) if model.project_id is not None else None,
            payload=payload,
            status=str(model.status),
            priority=int(model.priority),
            attempts=int(model.attempts),
            max_attempts=int(model.max_attempts),
            next_run_at=model.next_run_at,
            locked_by=str(model.locked_by) if model.locked_by else None,
            locked_at=model.locked_at,
            heartbeat_at=model.heartbeat_at,
            progress_done=int(model.progress_done or 0),
            progress_total=int(model.progress_total or 0),
            error_code=str(model.error_code) if model.error_code else None,
            error_message=str(model.error_message) if model.error_message else None,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )
