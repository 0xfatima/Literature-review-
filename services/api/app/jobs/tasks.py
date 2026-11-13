"""Celery tasks: thin wrappers that call services. Business logic does not live here."""

from __future__ import annotations

import logging
import time
import uuid

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.context import new_correlation_id, reset_correlation_id, set_correlation_id
from app.core.logging import configure_logging
from app.db.models import Job
from app.db.session import get_sessionmaker
from app.jobs import fsm
from app.jobs.celery_app import celery_app, send_task
from app.repositories import jobs as repo
from app.services import job_service, workflow_service

log = logging.getLogger(__name__)
MAX_PING_SLEEP_S = 60


def _job_settings(db: Session, job: Job) -> Settings:
    """Settings for one job: the job owner's own AI provider/model/key applied (user_llm_service.settings_for)."""
    from app.services import user_llm_service

    return user_llm_service.settings_for(db, get_settings(), job.owner_id)


@celery_app.task(name="system.ping")
def system_ping(job_id: str) -> str:
    """Round-trip proof that queue -> worker -> database works. Optional input.delay_s lets a human kill the
    worker mid-job to observe recovery (see README 'Manual verification')."""
    configure_logging(get_settings().log_level)
    token = set_correlation_id(new_correlation_id())
    try:
        jid = uuid.UUID(job_id)
        with get_sessionmaker()() as db:
            if not repo.claim(db, jid):
                db.rollback()
                log.info("duplicate/late delivery ignored", extra={"job_id": job_id})
                return "ignored"
            db.commit()
            job = repo.get_by_id(db, jid)
            if job is None:  # claimed row vanished (deleted between claim and read): nothing to do
                log.warning("claimed job no longer exists", extra={"job_id": job_id})
                return "ignored"
            delay = min(int((job.input or {}).get("delay_s", 0)), MAX_PING_SLEEP_S)
            for i in range(delay):
                time.sleep(1)
                db.refresh(job)
                if job.cancel_requested:
                    repo.finish(db, jid, fsm.RUNNING, fsm.CANCELLED)
                    db.commit()
                    return "cancelled"
                repo.set_progress(db, jid, int(90 * (i + 1) / max(delay, 1)), f"waiting {i + 1}/{delay}s")
                db.commit()
            repo.finish(db, jid, fsm.RUNNING, fsm.SUCCEEDED, result={"pong": True})
            db.commit()
        return "ok"
    finally:
        reset_correlation_id(token)


@celery_app.task(name="system.pump_jobs")
def pump_jobs() -> int:
    """Phase 15: admit waiting jobs within the fairness limits (also runs after every finished job)."""
    with get_sessionmaker()() as db:
        return job_service.pump(db, get_settings(), send_task)


@celery_app.task(name="system.sweep_jobs")
def sweep_jobs() -> dict[str, int]:
    configure_logging(get_settings().log_level)
    with get_sessionmaker()() as db:
        out = job_service.sweep(db, get_settings(), send_task)
    with get_sessionmaker()() as db:  # Phase 13: resume pipeline runs whose awaited job/backoff is over
        from app.pipeline import service as pipeline_service

        try:
            out["pipelines_woken"] = pipeline_service.wake(db, get_settings(), send_task)
        except Exception:  # noqa: BLE001  the job sweep result must still be returned
            db.rollback()
            log.exception("pipeline wake failed")
    return out


@celery_app.task(name="workflow.advance")
def workflow_advance(job_id: str) -> str:
    """Advance the review workflow by one human step. All logic is in app.workflows.runtime/workflow_service."""
    from app.workflows import runtime  # imported lazily: the API process never needs LangGraph's checkpointer

    configure_logging(get_settings().log_level)
    token = set_correlation_id(new_correlation_id())
    try:
        jid = uuid.UUID(job_id)
        with get_sessionmaker()() as db:
            if not repo.claim(db, jid):
                db.rollback()
                log.info("duplicate/late delivery ignored", extra={"job_id": job_id})
                return "ignored"
            db.commit()
            job = repo.get_by_id(db, jid)
            if job is None:
                return "ignored"
            run_id = job.workflow_run_id
            try:
                outcome = runtime.advance(db, job)
            except Exception as e:  # noqa: BLE001  any failure must end visibly, never leave the run "running"
                db.rollback()
                log.exception("workflow step failed", extra={"job_id": job_id})
                summary = f"The workflow step failed ({type(e).__name__}). Restart the workflow to continue."
                if run_id is not None:
                    workflow_service.fail(db, run_id, summary)
                repo.finish(db, jid, fsm.RUNNING, fsm.FAILED, error=type(e).__name__)
                db.commit()
                return "failed"
            repo.finish(db, jid, fsm.RUNNING, fsm.SUCCEEDED, result={"outcome": outcome})
            db.commit()
            return outcome
    finally:
        reset_correlation_id(token)


@celery_app.task(name="search.run")
def search_run(job_id: str) -> str:
    """Run or resume one scholarly search. All logic is in app.services.search_executor."""
    from app.services import search_executor, search_service

    configure_logging(get_settings().log_level)
    token = set_correlation_id(new_correlation_id())
    try:
        jid = uuid.UUID(job_id)
        with get_sessionmaker()() as db:
            if not repo.claim(db, jid):
                db.rollback()
                log.info("duplicate/late delivery ignored", extra={"job_id": job_id})
                return "ignored"
            db.commit()
            job = repo.get_by_id(db, jid)
            if job is None:
                return "ignored"
            try:
                return search_executor.execute(db, job, get_settings()).status
            except Exception as e:  # noqa: BLE001  any failure must end visibly; stored pages are kept
                db.rollback()
                log.exception("search job failed", extra={"job_id": job_id})
                repo.finish(db, jid, fsm.RUNNING, fsm.FAILED, error=f"Search failed ({type(e).__name__}).")
                db.commit()
                from app.repositories import search as search_repo

                run_id = (job.input or {}).get("search_run_id")
                run = search_repo.get_run_by_id(db, uuid.UUID(str(run_id))) if run_id else None
                if run is not None and search_service.reconcile(db, run):
                    db.commit()
                return "failed"
    finally:
        reset_correlation_id(token)


@celery_app.task(name="relevance.assess")
def relevance_assess(job_id: str) -> str:
    """Lexical + optional AI relevance assessment. All logic is in app.services.relevance_service."""
    from app.services import relevance_service

    configure_logging(get_settings().log_level)
    token = set_correlation_id(new_correlation_id())
    try:
        jid = uuid.UUID(job_id)
        with get_sessionmaker()() as db:
            if not repo.claim(db, jid):
                db.rollback()
                log.info("duplicate/late delivery ignored", extra={"job_id": job_id})
                return "ignored"
            db.commit()
            job = repo.get_by_id(db, jid)
            if job is None:
                return "ignored"
            try:
                return relevance_service.execute(db, job, _job_settings(db, job))
            except Exception as e:  # noqa: BLE001  end visibly; assessments committed so far are kept
                db.rollback()
                log.exception("relevance job failed", extra={"job_id": job_id})
                repo.finish(
                    db, jid, fsm.RUNNING, fsm.FAILED, error=f"Relevance assessment failed ({type(e).__name__})."
                )
                db.commit()
                return "failed"
    finally:
        reset_correlation_id(token)


@celery_app.task(name="fulltext.fetch_oa")
def fulltext_fetch_oa(job_id: str) -> str:
    """Download open-access PDFs for papers awaiting full-text review. Logic: app.services.oa_fetch_service."""
    from app.services import oa_fetch_service

    configure_logging(get_settings().log_level)
    token = set_correlation_id(new_correlation_id())
    try:
        jid = uuid.UUID(job_id)
        with get_sessionmaker()() as db:
            if not repo.claim(db, jid):
                db.rollback()
                return "ignored"
            db.commit()
            job = repo.get_by_id(db, jid)
            if job is None:
                return "ignored"
            try:
                return oa_fetch_service.execute(db, job, get_settings())
            except Exception as e:  # noqa: BLE001  end visibly; PDFs stored so far are kept
                db.rollback()
                log.exception("open-access fetch failed", extra={"job_id": job_id})
                repo.finish(db, jid, fsm.RUNNING, fsm.FAILED, error=f"Fetching PDFs failed ({type(e).__name__}).")
                db.commit()
                return "failed"
    finally:
        reset_correlation_id(token)


@celery_app.task(name="fulltext.ai_screen")
def fulltext_ai_screen(job_id: str) -> str:
    """AI full-text screening of available PDFs. Logic: app.services.ft_ai_service."""
    from app.services import ft_ai_service

    configure_logging(get_settings().log_level)
    token = set_correlation_id(new_correlation_id())
    try:
        jid = uuid.UUID(job_id)
        with get_sessionmaker()() as db:
            if not repo.claim(db, jid):
                db.rollback()
                return "ignored"
            db.commit()
            job = repo.get_by_id(db, jid)
            if job is None:
                return "ignored"
            try:
                return ft_ai_service.execute(db, job, _job_settings(db, job))
            except Exception as e:  # noqa: BLE001  end visibly; decisions so far are kept
                db.rollback()
                log.exception("AI full-text screening failed", extra={"job_id": job_id})
                repo.finish(
                    db, jid, fsm.RUNNING, fsm.FAILED, error=f"AI full-text screening failed ({type(e).__name__})."
                )
                db.commit()
                return "failed"
    finally:
        reset_correlation_id(token)


@celery_app.task(name="document.process")
def document_process(job_id: str) -> str:
    """Read one uploaded PDF. All logic is in app.services.document_executor."""
    from app.services import document_executor

    configure_logging(get_settings().log_level)
    token = set_correlation_id(new_correlation_id())
    try:
        jid = uuid.UUID(job_id)
        with get_sessionmaker()() as db:
            if not repo.claim(db, jid):
                db.rollback()
                log.info("duplicate/late delivery ignored", extra={"job_id": job_id})
                return "ignored"
            db.commit()
            job = repo.get_by_id(db, jid)
            if job is None:
                return "ignored"
            try:
                return document_executor.execute(db, job, get_settings())
            except Exception as e:  # noqa: BLE001  end visibly: the document must not stay "processing"
                db.rollback()
                log.exception("document job failed", extra={"job_id": job_id})
                repo.finish(db, jid, fsm.RUNNING, fsm.FAILED, error=f"Document processing failed ({type(e).__name__}).")
                db.commit()
                document_executor.on_crash(db, job, e)
                return "failed"
    finally:
        reset_correlation_id(token)


@celery_app.task(name="extraction.run")
def extraction_run(job_id: str) -> str:
    """AI evidence extraction with provenance. All logic is in app.services.extraction_service."""
    from app.services import extraction_service

    configure_logging(get_settings().log_level)
    token = set_correlation_id(new_correlation_id())
    try:
        jid = uuid.UUID(job_id)
        with get_sessionmaker()() as db:
            if not repo.claim(db, jid):
                db.rollback()
                log.info("duplicate/late delivery ignored", extra={"job_id": job_id})
                return "ignored"
            db.commit()
            job = repo.get_by_id(db, jid)
            if job is None:
                return "ignored"
            try:
                return extraction_service.execute(db, job, _job_settings(db, job))
            except Exception as e:  # noqa: BLE001  end visibly; papers committed so far are kept
                db.rollback()
                log.exception("extraction job failed", extra={"job_id": job_id})
                repo.finish(db, jid, fsm.RUNNING, fsm.FAILED, error=f"Evidence extraction failed ({type(e).__name__}).")
                db.commit()
                return "failed"
    finally:
        reset_correlation_id(token)


@celery_app.task(name="appraisal.suggest")
def appraisal_suggest(job_id: str) -> str:
    """AI appraisal suggestions. All logic is in app.appraisal.service."""
    from app.appraisal import service

    configure_logging(get_settings().log_level)
    token = set_correlation_id(new_correlation_id())
    try:
        jid = uuid.UUID(job_id)
        with get_sessionmaker()() as db:
            if not repo.claim(db, jid):
                db.rollback()
                log.info("duplicate/late delivery ignored", extra={"job_id": job_id})
                return "ignored"
            db.commit()
            job = repo.get_by_id(db, jid)
            if job is None:
                return "ignored"
            try:
                return service.execute_suggestions(db, job, _job_settings(db, job))
            except Exception as e:  # noqa: BLE001  end visibly; work committed so far is kept
                db.rollback()
                log.exception("appraisal.suggest job failed", extra={"job_id": job_id})
                repo.finish(
                    db, jid, fsm.RUNNING, fsm.FAILED, error=f"Appraisal suggestions failed ({type(e).__name__})."
                )
                db.commit()
                return "failed"
    finally:
        reset_correlation_id(token)


@celery_app.task(name="synthesis.draft")
def synthesis_draft(job_id: str) -> str:
    """Draft a synthesis version. All logic is in app.synthesis.service."""
    from app.synthesis import service

    configure_logging(get_settings().log_level)
    token = set_correlation_id(new_correlation_id())
    try:
        jid = uuid.UUID(job_id)
        with get_sessionmaker()() as db:
            if not repo.claim(db, jid):
                db.rollback()
                log.info("duplicate/late delivery ignored", extra={"job_id": job_id})
                return "ignored"
            db.commit()
            job = repo.get_by_id(db, jid)
            if job is None:
                return "ignored"
            try:
                return service.execute_draft(db, job, _job_settings(db, job))
            except Exception as e:  # noqa: BLE001  end visibly; work committed so far is kept
                db.rollback()
                log.exception("synthesis.draft job failed", extra={"job_id": job_id})
                repo.finish(db, jid, fsm.RUNNING, fsm.FAILED, error=f"Synthesis draft failed ({type(e).__name__}).")
                db.commit()
                return "failed"
    finally:
        reset_correlation_id(token)


@celery_app.task(name="pipeline.advance")
def pipeline_advance(job_id: str) -> str:
    """Advance the review pipeline graph. All logic is in app.pipeline.runtime."""
    from app.db.models import PipelineRun
    from app.pipeline import runtime as pipeline_runtime

    configure_logging(get_settings().log_level)
    token = set_correlation_id(new_correlation_id())
    try:
        jid = uuid.UUID(job_id)
        with get_sessionmaker()() as db:
            if not repo.claim(db, jid):
                db.rollback()
                return "ignored"
            db.commit()
            job = repo.get_by_id(db, jid)
            if job is None:
                return "ignored"
            try:
                outcome = pipeline_runtime.advance(db, job)
            except Exception as e:  # noqa: BLE001  end visibly; the checkpoint keeps every completed step
                db.rollback()
                log.exception("pipeline step failed", extra={"job_id": job_id})
                run = db.get(PipelineRun, uuid.UUID(str((job.input or {}).get("run_id"))))
                if run is not None and run.status == "running":
                    run.status = "failed"
                    run.error_summary = f"A pipeline step failed ({type(e).__name__}). Recover to continue."
                    run.row_version += 1
                repo.finish(db, jid, fsm.RUNNING, fsm.FAILED, error=f"Pipeline step failed ({type(e).__name__}).")
                db.commit()
                return "failed"
            repo.finish(db, jid, fsm.RUNNING, fsm.SUCCEEDED, result={"run_status": outcome})
            db.commit()
            return outcome
    finally:
        reset_correlation_id(token)


@celery_app.task(name="system.purge_documents")
def purge_documents() -> dict[str, int]:
    """Retention sweep (scheduled by beat): stored files and orphans, then ended session rows (Phase 14)."""
    from app.providers.storage import build_store
    from app.services import auth_service, document_service

    configure_logging(get_settings().log_level)
    with get_sessionmaker()() as db:
        out = document_service.purge(db, get_settings(), build_store(get_settings()))
        out["sessions_deleted"] = auth_service.purge_sessions(db, get_settings())
        return out
