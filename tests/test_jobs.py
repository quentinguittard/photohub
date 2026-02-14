import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from photohub.db import create_session_factory, create_sqlite_engine, init_db
from photohub.models import JobQueue
from photohub.services.jobs import (
    JOB_STATUS_COMPLETED,
    JOB_STATUS_RETRY_WAITING,
    JOB_STATUS_RUNNING,
    JobQueueService,
)


class JobQueueServiceTests(unittest.TestCase):
    def test_retry_then_complete(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            engine = create_sqlite_engine(base / "db.sqlite")
            init_db(engine)
            sf = create_session_factory(engine)
            service = JobQueueService(sf, base_backoff_seconds=1, max_backoff_seconds=4)

            enqueued = service.enqueue(job_type="export", payload={"x": 1}, project_id=7)
            self.assertEqual(enqueued.status, "queued")

            claimed = service.claim_next(worker_id="worker-a")
            self.assertIsNotNone(claimed)
            assert claimed is not None
            self.assertEqual(claimed.status, JOB_STATUS_RUNNING)
            self.assertEqual(claimed.attempts, 1)

            failed = service.fail(
                job_id=claimed.id,
                worker_id="worker-a",
                error_message="boom",
            )
            self.assertEqual(failed.status, JOB_STATUS_RETRY_WAITING)
            self.assertEqual(failed.attempts, 1)

            with sf() as session:
                row = session.get(JobQueue, failed.id)
                assert row is not None
                row.next_run_at = datetime.utcnow() - timedelta(seconds=1)
                session.commit()

            claimed2 = service.claim_next(worker_id="worker-a")
            self.assertIsNotNone(claimed2)
            assert claimed2 is not None
            self.assertEqual(claimed2.id, failed.id)
            self.assertEqual(claimed2.attempts, 2)

            completed = service.complete(job_id=claimed2.id, worker_id="worker-a", message="ok")
            self.assertEqual(completed.status, JOB_STATUS_COMPLETED)

            counts = service.counts()
            self.assertEqual(counts.get("completed"), 1)
            events = service.list_job_events(completed.id)
            self.assertTrue(any("retry" in message.lower() for _level, message, _at in events))
            engine.dispose()

    def test_recover_stale_running_job(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            engine = create_sqlite_engine(base / "db.sqlite")
            init_db(engine)
            sf = create_session_factory(engine)
            service = JobQueueService(sf, base_backoff_seconds=1, max_backoff_seconds=4)

            enqueued = service.enqueue(job_type="ingest", payload={"src": "x"})
            claimed = service.claim_next(worker_id="worker-z")
            self.assertIsNotNone(claimed)

            with sf() as session:
                row = session.get(JobQueue, enqueued.id)
                assert row is not None
                row.status = JOB_STATUS_RUNNING
                row.heartbeat_at = datetime.utcnow() - timedelta(seconds=600)
                session.commit()

            recovered = service.recover_stale_running_jobs(stale_after_seconds=1)
            self.assertEqual(recovered, 1)
            refreshed = service.get_job(enqueued.id)
            self.assertIsNotNone(refreshed)
            assert refreshed is not None
            self.assertIn(refreshed.status, {"retry_waiting", "failed"})
            engine.dispose()


if __name__ == "__main__":
    unittest.main()

