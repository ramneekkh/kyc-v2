# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

"""
Perpetual KYC (pKYC) ongoing-monitoring job.

This replaces the previous APScheduler implementation, which was broken in two
independent ways:

  1. `subjects = []` was hardcoded, so the daily job iterated nothing. Ongoing
     monitoring - a standing regulatory obligation under FATF Recommendation 10
     and MAS Notice 626 - had never actually run, while the UI implied it had.

  2. `init_scheduler` ran at import time. Under `gunicorn -w 4` that produced four
     independent schedulers in four processes, each firing the same job, so any
     work it did do would have been quadrupled.

The job is now driven externally by Cloud Scheduler hitting an authenticated HTTP
endpoint. One scheduler, one trigger, visible execution history, and retries that
are the platform's problem rather than an in-process timer's.
"""

import datetime
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

from .database.spanner_client import spanner_client
from .events.publisher import publish_kyc_job

logger = logging.getLogger(__name__)

# How often each subject must be re-screened. Risk-tiered: a high-risk or PEP
# relationship warrants more frequent review than a standard retail customer.
MONITORING_INTERVAL_DAYS = {
    "high": int(os.environ.get("KYC_MONITOR_DAYS_HIGH", 1)),
    "medium": int(os.environ.get("KYC_MONITOR_DAYS_MEDIUM", 7)),
    "low": int(os.environ.get("KYC_MONITOR_DAYS_LOW", 30)),
    "unknown": int(os.environ.get("KYC_MONITOR_DAYS_UNKNOWN", 1)),
}

# Cap per invocation so one run cannot exhaust the daily search quota.
MAX_SUBJECTS_PER_RUN = int(os.environ.get("KYC_MONITOR_MAX_SUBJECTS", 200))

MONITOR_WORKERS = int(os.environ.get("KYC_MONITOR_WORKERS", 3))


def _risk_tier(subject: Dict) -> str:
    """Maps a subject's last known risk score to a monitoring cadence bucket."""
    summary = subject.get("summary")
    if isinstance(summary, str):
        import json
        try:
            summary = json.loads(summary)
        except (ValueError, TypeError):
            summary = {}
    if not isinstance(summary, dict):
        summary = {}

    score = str(summary.get("risk_score", "")).strip().lower()
    if score in ("high", "medium", "low"):
        return score
    # No score, or an explicit "Unknown" from a fail-closed run: re-screen daily.
    # An unscreened subject is the one most in need of monitoring.
    return "unknown"


def _is_due(subject: Dict, now: datetime.datetime) -> bool:
    """True when the subject's monitoring interval has elapsed."""
    last_run = subject.get("updated_at")
    if not last_run:
        return True

    if isinstance(last_run, str):
        try:
            last_run = datetime.datetime.fromisoformat(last_run.replace("Z", "+00:00"))
        except ValueError:
            return True

    if last_run.tzinfo:
        reference = now.astimezone(datetime.timezone.utc)
    else:
        reference = now.replace(tzinfo=None)

    interval = MONITORING_INTERVAL_DAYS[_risk_tier(subject)]
    return (reference - last_run).days >= interval


def select_subjects_due_for_monitoring(limit: int = MAX_SUBJECTS_PER_RUN) -> List[Dict]:
    """
    Returns subjects whose monitoring interval has elapsed, most-overdue first.

    This is the piece that was missing entirely: the previous job had no way to
    enumerate what it was supposed to monitor.
    """
    try:
        subjects = spanner_client.get_all_subjects()
    except Exception as e:
        logger.error(f"pKYC: could not enumerate subjects: {e}")
        return []

    now = datetime.datetime.now(datetime.timezone.utc)
    due = [s for s in subjects if _is_due(s, now)]

    # Oldest first so nothing starves behind the cap.
    due.sort(key=lambda s: str(s.get("updated_at") or ""))
    selected = due[:limit]

    logger.info(
        f"pKYC: {len(due)} of {len(subjects)} subjects are due for re-screening; "
        f"dispatching {len(selected)} (cap {limit})."
    )
    if len(due) > limit:
        logger.warning(
            f"pKYC: {len(due) - limit} due subjects deferred to the next run. "
            "Raise KYC_MONITOR_MAX_SUBJECTS or increase run frequency if this persists."
        )
    return selected


def run_daily_kyc_job(limit: int = MAX_SUBJECTS_PER_RUN) -> Dict:
    """
    Enqueues an incremental re-screen for every subject that is due.

    Work is published to Pub/Sub rather than executed inline so that a slow or
    failing subject cannot stall the scheduler request, and so retries are handled
    by the subscription rather than lost.
    """
    started = datetime.datetime.now(datetime.timezone.utc)
    logger.info("pKYC: starting ongoing monitoring sweep.")

    subjects = select_subjects_due_for_monitoring(limit)
    if not subjects:
        logger.info("pKYC: no subjects due for monitoring.")
        return {"dispatched": 0, "failed": 0, "considered": 0}

    dispatched = 0
    failed = 0

    def _dispatch(subject: Dict) -> bool:
        name = subject.get("name") or subject.get("Name")
        subject_id = subject.get("subject_id") or subject.get("SubjectId")
        if not name or not subject_id:
            logger.warning(f"pKYC: skipping malformed subject record: {subject}")
            return False
        try:
            payload = {
                "name": name,
                "subject_id": subject_id,
                "customer_id": subject.get("customer_id"),
                "key_strategy": subject.get("key_strategy"),
                "trigger": "pkyc_scheduled",
            }
            msg_id = publish_kyc_job(payload, "incremental")
            if msg_id:
                logger.info(f"pKYC: queued {name} ({subject_id}) as {msg_id}.")
                return True
            logger.error(f"pKYC: publish returned no message id for {name}.")
            return False
        except Exception as e:
            logger.error(f"pKYC: failed to queue {name}: {e}")
            return False

    with ThreadPoolExecutor(max_workers=MONITOR_WORKERS) as executor:
        futures = [executor.submit(_dispatch, s) for s in subjects]
        for future in as_completed(futures):
            if future.result():
                dispatched += 1
            else:
                failed += 1

    elapsed = (datetime.datetime.now(datetime.timezone.utc) - started).total_seconds()
    result = {
        "dispatched": dispatched,
        "failed": failed,
        "considered": len(subjects),
        "elapsed_seconds": round(elapsed, 2),
        "started_at": started.isoformat(),
    }

    if failed:
        # A monitoring sweep that partially failed is a compliance gap, not a warning.
        logger.error(f"pKYC: sweep completed with {failed} dispatch failures: {result}")
    else:
        logger.info(f"pKYC: sweep complete: {result}")
    return result


def init_scheduler(app=None):
    """
    Deliberately a no-op.

    Ongoing monitoring is triggered by Cloud Scheduler calling
    POST /api/tasks/pkyc-sweep with an OIDC token. Starting an in-process scheduler
    here would run one copy per gunicorn worker.

    Provision with:

        gcloud scheduler jobs create http kyc-pkyc-sweep \\
          --schedule="0 2 * * *" \\
          --time-zone="Asia/Singapore" \\
          --uri="https://<service-url>/api/tasks/pkyc-sweep" \\
          --http-method=POST \\
          --oidc-service-account-email=<scheduler-sa>@<project>.iam.gserviceaccount.com \\
          --oidc-token-audience="https://<service-url>"
    """
    logger.info(
        "In-process scheduler intentionally disabled; pKYC runs via Cloud Scheduler "
        "against /api/tasks/pkyc-sweep."
    )
    return None
