
import logging
import os
from google.cloud import spanner
from .spanner_client import spanner_client

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def purge_database():
    """
    Deletes ALL data from the database tables in dependency order.
    """
    logger.info("Starting database purge...")
    
    # Ensure connection
    spanner_client.connect()
    if not spanner_client.database:
        logger.error("Could not connect to database. Aborting purge.")
        return

    # Order matters due to Foreign Keys:
    # 1. GraphEdges (FK to GraphNodes)
    # 2. GraphNodes (FK to Subjects)
    # 3. Findings (FK to Subjects)
    # 4. Subjects (Root)
    tables_to_purge = ["GraphEdges", "GraphNodes", "Findings", "Subjects"]

    def delete_all_rows(transaction):
        for table in tables_to_purge:
            logger.info(f"Deleting all rows from {table}...")
            # KeySet(all_=True) deletes all rows
            transaction.delete(table, spanner.KeySet(all_=True))
            logger.info(f"Scheduled delete for {table}.")

    try:
        spanner_client.database.run_in_transaction(delete_all_rows)
        logger.info("Database purge completed successfully. All data deleted.")
    except Exception as e:
        logger.error(f"Error during database purge: {e}")

if __name__ == "__main__":
    purge_database()
