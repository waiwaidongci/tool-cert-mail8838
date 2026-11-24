import hashlib
import json
import logging
from datetime import date, timedelta
from sqlalchemy import and_, or_, select
from src.config.database import Session
from src.constants.enums import Status
from src.domain.analysis import assemble, line_report, prepare
from src.domain.spec import SPEC
from src.models.jobs import Delivery, Publication, Run, RunProgress, Schedule
from src.models.source import Dataset
from src.services.audit import record
from src.utils.exports import write_report
from src.utils.time import now

log = logging.getLogger(__name__)


def materialize_due():
    if not SPEC.get('scheduled'):
        return
    stamp = now()
    with Session.begin() as db:
        schedules = db.scalars(select(Schedule).where(Schedule.enabled.is_(True), Schedule.next_at <= stamp)
                                .order_by(Schedule.next_at).limit(20).with_for_update(skip_locked=True))
        for item in schedules:
            due = item.next_at
            dataset_id = item.dataset_id
            params = dict(item.params)
            if item.repeat_daily:
                elapsed_days = (stamp - due).days
                due += timedelta(days=elapsed_days)
                period_key = SPEC['schedule_date_param']
                report_day = date.fromisoformat(params[period_key]) + timedelta(days=elapsed_days)
                params[period_key] = report_day.isoformat()
                item.params = {**params, period_key: (report_day + timedelta(days=1)).isoformat()}
                dataset_id = db.scalar(select(Dataset.id).order_by(Dataset.id.desc()).limit(1)) or dataset_id
                item.next_at = due + timedelta(days=1)
            else:
                item.enabled = False
            db.add(Run(dataset_id=dataset_id, schedule_id=item.id, scheduled_at=due,
                       name=item.name, params=params, author=item.author))


def claim():
    stamp = now()
    with Session.begin() as db:
        query = select(Run).where(or_(Run.status == Status.QUEUED,
                                      and_(Run.status == Status.RUNNING, Run.lease_until < stamp)))
        job = db.scalar(query.order_by(Run.id).limit(1).with_for_update(skip_locked=True))
        if job is None:
            return None
        if job.attempt >= 3:
            job.status = Status.FAILED
            job.error = '后台进程多次中断，请发起新的运行'
            job.finished_at = stamp
            record(db, 'worker', 'analysis.failed', job.id, reason=job.error)
            return None
        job.status = Status.RUNNING
        job.attempt += 1
        job.lease_until = stamp + timedelta(minutes=3)
        records = db.get(Dataset, job.dataset_id).rows
        return job.id, job.attempt, records, job.params


def _fingerprint(rows, params):
    raw = json.dumps({'rows': rows, 'params': params}, ensure_ascii=False,
                     sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _donor_lines(db, job, signature):
    """同一批次与参数下已中断的任务可提供已算好的产线进度，重试无需重算。"""
    candidates = db.scalars(select(RunProgress).join(Run, Run.id == RunProgress.run_id)
                            .where(RunProgress.fingerprint == signature, Run.status == Status.FAILED,
                                   Run.dataset_id == job.dataset_id, Run.id != job.id)
                            .order_by(RunProgress.updated_at.desc()).limit(10))
    for item in candidates:
        donor = db.get(Run, item.run_id)
        if donor and donor.params == job.params:
            return dict(item.lines or {})
    return {}


def _load_progress(db, job, signature, total):
    progress = db.get(RunProgress, job.id, with_for_update=True)
    if progress is None:
        progress = RunProgress(run_id=job.id, attempt=job.attempt, fingerprint=signature,
                               lines=_donor_lines(db, job, signature), cursor=0, total=total)
        db.add(progress)
        db.flush()
    elif progress.fingerprint != signature:
        progress.fingerprint = signature
        progress.lines = {}
    progress.attempt = job.attempt
    progress.total = total
    progress.cursor = len(progress.lines or {})
    progress.updated_at = now()
    return progress


def _checkpoint(job_id, attempt, line):
    with Session.begin() as db:
        job = db.get(Run, job_id, with_for_update=True)
        if job is None or job.attempt != attempt or job.status != Status.RUNNING:
            return False
        progress = db.get(RunProgress, job_id, with_for_update=True)
        if progress is None or progress.attempt != attempt:
            return False
        progress.lines = {**(progress.lines or {}), line['line_code']: line}
        progress.cursor = len(progress.lines)
        progress.updated_at = now()
        job.lease_until = now() + timedelta(minutes=3)
        return True


def _current(db):
    return db.scalar(select(Publication).order_by(Publication.dataset_id.desc(), Publication.run_id.desc())
                     .limit(1).with_for_update())


def _publish(db, job, result):
    """结果与数据依据同批落库；较旧批次的续算任务不能覆盖较新的发布。"""
    current = _current(db)
    if current is not None and (current.dataset_id, current.run_id) > (job.dataset_id, job.id):
        return False
    existing = db.scalar(select(Publication).where(Publication.run_id == job.id).with_for_update())
    if existing is not None:
        existing.dataset_id = job.dataset_id
        existing.report_day = job.params.get(SPEC['schedule_date_param'], '')
        existing.result = result
        existing.published_at = now()
    else:
        db.add(Publication(run_id=job.id, dataset_id=job.dataset_id,
                           report_day=job.params.get(SPEC['schedule_date_param'], ''), result=result))
    return True


def _finish(job_id, attempt, result, export, failure):
    with Session.begin() as db:
        job = db.get(Run, job_id, with_for_update=True)
        if job is None or job.attempt != attempt or job.status != Status.RUNNING:
            return
        job.lease_until = None
        job.finished_at = now()
        job.status = Status.FAILED if failure else Status.COMPLETE
        job.result = None if failure else result
        job.export_file = export
        job.error = failure
        record(db, 'worker', f'analysis.{job.status}', job.id, attempt=attempt, reason=failure)
        if failure:
            if job.schedule_id:
                plan = db.get(Schedule, job.schedule_id)
                receipt = Delivery(run_id=job.id, recipient=plan.recipient, state='blocked', detail=failure)
                db.add(receipt)
                record(db, 'worker', 'delivery.blocked', job.id, recipient=plan.recipient)
            return
        published = _publish(db, job, result)
        if not published:
            record(db, 'worker', 'publication.superseded', job.id, dataset_id=job.dataset_id)
        if not job.schedule_id:
            return
        plan = db.get(Schedule, job.schedule_id)
        if not published:
            record(db, 'worker', 'delivery.superseded', job.id, recipient=plan.recipient)
            return
        ready = result.get('ready', True)
        detail = '已保存 CSV 到站内收件箱' if ready else result['headline']
        receipt = Delivery(run_id=job.id, recipient=plan.recipient,
                           state='delivered' if ready else 'blocked', detail=detail[:500])
        db.add(receipt)
        record(db, 'worker', f'delivery.{receipt.state}', job.id, recipient=plan.recipient)


def execute(lease):
    job_id, attempt, rows, params = lease
    signature = _fingerprint(rows, params)
    result = export = failure = None
    try:
        context = prepare(rows, params)
        with Session.begin() as db:
            job = db.get(Run, job_id, with_for_update=True)
            if job is None or job.attempt != attempt or job.status != Status.RUNNING:
                return
            progress = _load_progress(db, job, signature, len(context['line_codes']))
            done = dict(progress.lines or {})
        for code in context['line_codes']:
            if code in done:
                continue
            line = line_report(context, code)
            if not _checkpoint(job_id, attempt, line):
                return
            done[code] = line
        result = assemble(context, done)
        export = write_report(job_id, attempt, result)
    except ValueError as error:
        failure = str(error)[:500]
    except Exception:
        log.exception('Analysis %s failed', job_id)
        failure = '计算失败，请检查字段口径后重试'
    _finish(job_id, attempt, result, export, failure)
