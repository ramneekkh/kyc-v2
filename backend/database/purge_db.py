
import logging
import os
from google.cloud import spanner
from .spanner_client import spanner_client

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def purge_database(confirmed: bool = False, expected_database: str = None):
    """
    Deletes ALL data from the database tables in dependency order.

    Requires explicit confirmation. Running `python -m backend.database.purge_db`
    previously wiped every subject, finding and graph in whatever database the
    ambient environment pointed at - including production - with no prompt, no
    dry run and no record that it happened. Screening records are retained
    evidence; destroying them is a reportable event, not a convenience.
    """
    target = os.getenv("SPANNER_DATABASE")
    instance = os.getenv("SPANNER_INSTANCE")
    project = os.getenv("GOOGLE_CLOUD_PROJECT")

    if not confirmed:
        logger.error(
            "Refusing to purge: confirmation required. "
            "Call purge_database(confirmed=True, expected_database=<name>) or run "
            "with --yes --database <name>."
        )
        return False

    if expected_database and expected_database != target:
        logger.error(
            f"Refusing to purge: --database '{expected_database}' does not match the "
            f"configured SPANNER_DATABASE '{target}'. This guard exists to stop a "
            "staging purge from landing on production."
        )
        return False

    if not expected_database:
        logger.error(
            "Refusing to purge: you must name the target database explicitly so the "
            "intended target cannot be supplied by ambient environment variables."
        )
        return False

    logger.warning(
        f"PURGING ALL KYC DATA from {project}/{instance}/{target}. "
        "This destroys retained screening evidence."
    )

    # Ensure connection
    spanner_client.connect()
    if not spanner_client.database:
        logger.error("Could not connect to database. Aborting purge.")
        return False

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
        logger.warning(
            f"Database purge completed. All data deleted from "
            f"{project}/{instance}/{target}."
        )
        return True
    except Exception as e:
        logger.error(f"Error during database purge: {e}")
        return False


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Delete ALL KYC data from the configured Spanner database."
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm the destructive operation.",
    )
    parser.add_argument(
        "--database",
        required=True,
        help="Name of the target database. Must match SPANNER_DATABASE exactly.",
    )
    args = parser.parse_args()

    if not args.yes:
        answer = input(
            f"Delete ALL KYC data from '{args.database}'? "
            "This cannot be undone. Type the database name to confirm: "
        )
        if answer.strip() != args.database:
            logger.error("Confirmation did not match. Aborting.")
            raise SystemExit(1)

    ok = purge_database(confirmed=True, expected_database=args.database)
    raise SystemExit(0 if ok else 1)
