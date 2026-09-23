#!/bin/bash
# run_backend.sh

# Load .env safely.
#
# `export $(cat .env | xargs)` is broken in three ways that matter here: it
# word-splits on spaces (so any value containing one is truncated or turns into
# a second bogus variable), it strips quoting, and it passes the contents
# through shell expansion -- a `$` or backtick in a secret gets evaluated.
# `set -a` + `.` makes the shell parse the file as assignments, which is what
# was intended.
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

export FLASK_APP=backend.app
export PORT=${PORT:-8080}

echo "Starting backend server with Gunicorn on port $PORT..."
echo "Timeout set to 300 seconds (5 minutes)."

# Run schema migration before starting the server
echo "Running database schema migration..."
python3 -m backend.database.schema_manager

# Ensure venv is activated for gunicorn
if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
fi

# Run gunicorn with increased timeout (removed --preload for gRPC compatibility)
exec gunicorn -w 4 -b 0.0.0.0:$PORT --timeout 1200 backend.app:app --log-level info --access-logfile - --error-logfile -
