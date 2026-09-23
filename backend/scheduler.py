import logging
import time
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from concurrent.futures import ThreadPoolExecutor
# from .database.spanner_client import spanner_client # Will fetch subjects from DB
from .search.core import run_kyc_process

logger = logging.getLogger(__name__)

def run_daily_kyc_job():
    """
    Job that runs daily.
    1. Fetches all active subjects (mocked list for now or from Spanner).
    2. Runs incremental KYC for each.
    """
    logger.info("Starting Daily KYC Monitoring Job...")
    
    # In a real implementation:
    # subjects = spanner_client.get_active_subjects()
    
    # For prototype, we monitor a hardcoded list or just log correct execution
    subjects = [] # spanner_client.list_active_subjects() if implemented
    
    if not subjects:
        logger.info("No active subjects found for daily monitoring.")
        return

    def process_subject(subj):
        try:
            filters = {
                "subject_name": subj['Name'],
                "subject_id": subj['SubjectId'],
                "incremental": True, # Optimize search
                "recency_days": 1, # Only look for yesterday's news
                "num_results": 5 
            }
            logger.info(f"Running incremental check for {subj['Name']}...")
            for _ in run_kyc_process(filters):
                pass
        except Exception as e:
            logger.error(f"Error in daily job for {subj['Name']}: {e}")

    with ThreadPoolExecutor(max_workers=3) as executor:
        executor.map(process_subject, subjects)
        
    logger.info("Daily KYC Monitoring Job Completed.")

def init_scheduler(app):
    scheduler = BackgroundScheduler()
    # Run every 24 hours
    scheduler.add_job(
        func=run_daily_kyc_job,
        trigger=IntervalTrigger(hours=24),
        id='daily_kyc_job',
        name='Daily KYC monitoring',
        replace_existing=True
    )
    scheduler.start()
    logger.info("APScheduler started.")
