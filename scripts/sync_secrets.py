#!/usr/bin/env python3
"""
Synchronizes non-ADC KYC-v2 secrets into Google Cloud Secret Manager.

Gemini authenticates via ADC and needs no secret. Everything else does:
  - GOOGLE_CSE_ID           Custom Search engine id
  - GOOGLE_SEARCH_API_KEY   Custom Search API key
  - GOOGLE_API_KEY          legacy call paths only
  - SB_API_KEY              ScrapingBee

Values are read from the environment (or a git-ignored .env), never from this
file. Export them first:

  export GOOGLE_CSE_ID=... GOOGLE_SEARCH_API_KEY=... SB_API_KEY=...
  python3 scripts/sync_secrets.py [--project PROJECT_ID]
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.secret_manager import DEFAULT_SECRET_FALLBACKS, get_project_id, upsert_secret_in_gsm

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def main():
    parser = argparse.ArgumentParser(description="Store KYC-v2 secrets in Google Cloud Secret Manager")
    parser.add_argument(
        "--project",
        default=get_project_id() or "elevate-data-508005",
        help="GCP Project ID (default: resolved from ADC / GOOGLE_CLOUD_PROJECT)",
    )
    args = parser.parse_args()

    logging.info(f"Target GCP Project for Secret Manager: {args.project}")
    for secret_id, secret_value in DEFAULT_SECRET_FALLBACKS.items():
        try:
            version_name = upsert_secret_in_gsm(
                secret_id=secret_id,
                secret_value=secret_value,
                project_id=args.project,
            )
            logging.info(f"✅ Stored {secret_id} -> {version_name}")
        except Exception as exc:
            logging.error(f"❌ Failed to store {secret_id} in project '{args.project}': {exc}")
            sys.exit(1)

    logging.info("All secrets successfully stored in Google Cloud Secret Manager!")


if __name__ == "__main__":
    main()
