#!/bin/sh
# With no command: migrate, then serve the API. With a command (the `worker` service runs
# `python -m app.worker`): run it as-is, without migrating; the api service migrates.
set -e
if [ "$#" -gt 0 ]; then
  exec "$@"
fi
alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
