import os
import tempfile
import unittest
from datetime import timedelta
from unittest import mock

DB_FILE = tempfile.NamedTemporaryFile(suffix='.sqlite3', delete=False).name
ARTIFACT_DIR = tempfile.mkdtemp(prefix='artifacts-')
os.environ.setdefault('DATABASE_URL', f'sqlite:///{DB_FILE}')
os.environ.setdefault('JWT_SECRET', 'test-secret')
os.environ.setdefault('SEED_DEMO', 'false')
os.environ.setdefault('REDIS_URL', 'redis://127.0.0.1:6399/0')
os.environ.setdefault('ARTIFACT_DIR', ARTIFACT_DIR)

from src.config.database import Base, Session, engine  # noqa: E402
from src.constants.enums import Status  # noqa: E402
from src.domain.demo import demo_rows  # noqa: E402
from src.models.identity import Audit  # noqa: E402
from src.models.jobs import Delivery, Publication, Run, RunProgress, Schedule  # noqa: E402
from src.models.source import Dataset  # noqa: E402
from src.services import execution  # noqa: E402
from src.utils.time import now  # noqa: E402

PARAMS = {'report_day': '2026-11-21', 'horizon_days': 14, 'min_coverage_percent': 80}


class ExecutionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(engine)

    def setUp(self):
        with Session.begin() as db:
            for table in (Delivery, Publication, RunProgress, Run, Schedule, Dataset):
                db.query(table).delete()

    def dataset(self, name='批次'):
        with Session.begin() as db:
            item = Dataset(name=name, filename='demo.csv', checksum=name, rows=demo_rows(),
                           row_count=len(demo_rows()), author='tester')
            db.add(item)
            db.flush()
            return item.id

    def queue(self, dataset_id):
        with Session.begin() as db:
            item = Run(dataset_id=dataset_id, name='班报', params=dict(PARAMS), author='tester')
            db.add(item)
            db.flush()
            return item.id

    def claim(self):
        lease = execution.claim()
        self.assertIsNotNone(lease)
        return lease

    def run_status(self, run_id):
        with Session() as db:
            return db.get(Run, run_id).status

    def current_publication(self):
        with Session() as db:
            return db.query(Publication).order_by(Publication.dataset_id.desc(), Publication.run_id.desc()).first()

    def test_complete_run_publishes_with_source_batch(self):
        dataset_id = self.dataset()
        run_id = self.queue(dataset_id)
        execution.execute(self.claim())
        with Session() as db:
            job = db.get(Run, run_id)
            self.assertEqual(job.status, Status.COMPLETE)
            progress = db.get(RunProgress, run_id)
            self.assertEqual(progress.cursor, 4)
            self.assertEqual(progress.total, 4)
        item = self.current_publication()
        self.assertEqual(item.dataset_id, dataset_id)
        self.assertEqual(item.run_id, run_id)
        self.assertEqual(item.result['progress'], {'lines_done': 4, 'lines_total': 4})

    def test_interrupted_run_resumes_without_repeating_combos(self):
        dataset_id = self.dataset()
        run_id = self.queue(dataset_id)
        lease = self.claim()
        calls = {'count': 0}
        original = execution.line_report

        def stop_after_two(context, code):
            calls['count'] += 1
            if calls['count'] > 2:
                raise SystemExit('模拟后台进程中断')
            return original(context, code)

        with mock.patch.object(execution, 'line_report', stop_after_two):
            with self.assertRaises(SystemExit):
                execution.execute(lease)
        self.assertEqual(self.run_status(run_id), Status.RUNNING)
        with Session.begin() as db:
            job = db.get(Run, run_id)
            job.lease_until = now() - timedelta(minutes=1)
            self.assertEqual(len(db.get(RunProgress, run_id).lines), 2)
        resumed = self.claim()
        self.assertEqual(resumed[1], lease[1] + 1)
        execution.execute(resumed)
        with Session() as db:
            progress = db.get(RunProgress, run_id)
            self.assertEqual(set(progress.lines), {'L-ASSY', 'L-FINAL', 'L-MACH', 'L-PACK'})
        item = self.current_publication()
        line = next(row for row in item.result['lines'] if row['line_code'] == 'L-ASSY')
        keys = [combo['key'] for combo in line['combos']]
        self.assertEqual(keys, ['QC-102', 'QC-103+QC-105'])
        self.assertEqual(len(keys), len(set(keys)))

    def test_retry_can_reuse_progress_from_failed_run(self):
        dataset_id = self.dataset()
        context = execution.prepare(demo_rows(), PARAMS)
        done_line = execution.line_report(context, 'L-MACH')
        failed = self.queue(dataset_id)
        with Session.begin() as db:
            job = db.get(Run, failed)
            job.status = Status.FAILED
            db.add(RunProgress(run_id=failed, attempt=1, fingerprint=execution._fingerprint(demo_rows(), PARAMS),
                               lines={'L-MACH': done_line}, cursor=1, total=4))
        fresh = self.queue(dataset_id)
        computed = []
        original = execution.line_report

        def track(context, code):
            computed.append(code)
            return original(context, code)

        with mock.patch.object(execution, 'line_report', track):
            execution.execute(self.claim())
        self.assertEqual(sorted(computed), ['L-ASSY', 'L-FINAL', 'L-PACK'])
        with Session() as db:
            progress = db.get(RunProgress, fresh)
            self.assertEqual(progress.cursor, 4)
            self.assertEqual(set(progress.lines), {'L-ASSY', 'L-FINAL', 'L-MACH', 'L-PACK'})

    def test_older_batch_cannot_overwrite_newer_publication(self):
        first = self.dataset('批次一')
        second = self.dataset('批次二')
        old_run = self.queue(first)
        new_run = self.queue(second)
        with Session.begin() as db:
            old = db.get(Run, old_run)
            old.status = Status.RUNNING
            old.attempt = 1
            new = db.get(Run, new_run)
            new.status = Status.RUNNING
            new.attempt = 1
        execution.execute((new_run, 1, demo_rows(), PARAMS))
        execution.execute((old_run, 1, demo_rows(), PARAMS))
        item = self.current_publication()
        self.assertEqual(item.dataset_id, second)
        self.assertEqual(item.run_id, new_run)
        with Session() as db:
            self.assertEqual(db.get(Run, old_run).status, Status.COMPLETE)
            events = [row.event for row in db.query(Audit).all()]
            self.assertIn('publication.superseded', events)

    def test_scheduled_older_batch_does_not_deliver(self):
        first = self.dataset('批次一')
        second = self.dataset('批次二')
        with Session.begin() as db:
            plan = Schedule(name='每日班报', dataset_id=first, params=dict(PARAMS), recipient='值班主管',
                            next_at=now(), repeat_daily=True, author='tester')
            db.add(plan)
            db.flush()
            older = Run(dataset_id=first, schedule_id=plan.id, name='旧批次', params=dict(PARAMS),
                        author='tester', status=Status.RUNNING, attempt=1)
            newer = Run(dataset_id=second, schedule_id=plan.id, name='新批次', params=dict(PARAMS),
                        author='tester', status=Status.RUNNING, attempt=1)
            db.add_all([older, newer])
            db.flush()
            older_id, newer_id = older.id, newer.id
        execution.execute((newer_id, 1, demo_rows(), PARAMS))
        execution.execute((older_id, 1, demo_rows(), PARAMS))
        with Session() as db:
            self.assertIsNotNone(db.query(Delivery).filter_by(run_id=newer_id).first())
            self.assertIsNone(db.query(Delivery).filter_by(run_id=older_id).first())

    def test_failed_scheduled_run_leaves_blocked_receipt(self):
        dataset_id = self.dataset()
        with Session.begin() as db:
            plan = Schedule(name='每日班报', dataset_id=dataset_id, params=dict(PARAMS), recipient='值班主管',
                            next_at=now(), repeat_daily=True, author='tester')
            db.add(plan)
            db.flush()
            run = Run(dataset_id=dataset_id, schedule_id=plan.id, name='班报', params=dict(PARAMS),
                      author='tester', status=Status.RUNNING, attempt=1)
            db.add(run)
            db.flush()
            run_id = run.id
        execution.execute((run_id, 1, [{'record_type': '未知'}], PARAMS))
        with Session() as db:
            receipt = db.query(Delivery).filter_by(run_id=run_id).one()
            self.assertEqual(receipt.state, 'blocked')
            self.assertIn('记录类型', receipt.detail)

    def test_validation_failure_marks_run_failed_without_publication(self):
        dataset_id = self.dataset()
        run_id = self.queue(dataset_id)
        self.claim()
        execution.execute((run_id, 1, [{'record_type': '未知'}], PARAMS))
        with Session() as db:
            job = db.get(Run, run_id)
            self.assertEqual(job.status, Status.FAILED)
            self.assertIn('记录类型', job.error)
        self.assertIsNone(self.current_publication())


if __name__ == '__main__':
    unittest.main()
