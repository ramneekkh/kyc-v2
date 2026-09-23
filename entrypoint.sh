#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
set -e

# --- DIAGNOSTIC STEP ---
# The following lines are added for debugging. They print a detailed
# list of all files and directories in the current working directory (/app)
# when the container starts. This will show us if the 'migrations'
# directory was correctly copied into the container image.
echo "--- DEBUG: Listing contents of working directory ($PWD) ---"
ls -la
echo "---------------------------------------------------------"


# --- Load Environment Variables ---
# Load variables from .env file if it exists
if [ -f .env ]; then
  echo "Loading environment variables from .env file..."
  set -a # Automatically export all variables
  . ./.env # Source the .env file
  set +a # Turn off auto-export
else
  echo "Warning: .env file not found. Ensure necessary environment variables are set."
fi

# Ensure the backend directory exists relative to the initial CWD
if [ ! -d "backend" ]; then
  echo "ERROR: 'backend' directory not found. Cannot proceed."
  exit 1
fi

# Set FLASK_APP environment variable to the full module path
# This tells Flask to find the 'app' object inside the 'backend' package.
export FLASK_APP=backend.app

# --- Database Migrations (REMOVED) ---
# The KYC application version does not use a database, so the migration
# step is no longer necessary and has been removed to resolve the startup error.
echo "Skipping database migrations for KYC application."


# --- Backend Server Start ---
echo "Starting backend server with Gunicorn... (from project root)"

# Default port, can be overridden by PORT environment variable
PORT="${PORT:-8080}"

echo "Attempting to start Gunicorn on port $PORT..."
# Run gunicorn from the project root, pointing to the 'app' object in the 'backend.app' module
exec gunicorn -w 4 -b 0.0.0.0:$PORT --timeout 1200 --preload backend.app:app --log-level warn --access-logfile - --error-logfile -
# Using '-' for logfiles sends them to stdout/stderr, which is common for containerized apps.
