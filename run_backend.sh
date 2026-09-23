#!/bin/bash
# run_backend.sh

# Set environment variables if needed (or load from .env)
if [ -f .env ]; then
  export $(cat .env | xargs)
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
