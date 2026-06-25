#!/usr/bin/env bash
set -euo pipefail

# Wait for Postgres before doing anything that touches the DB.
wait_for_db() {
  echo "Waiting for Postgres at ${POSTGRES_HOST:-postgres}:${POSTGRES_PORT:-5432}..."
  python - <<'PY'
import os, time, socket
host = os.environ.get("POSTGRES_HOST", "postgres")
port = int(os.environ.get("POSTGRES_PORT", "5432"))
for _ in range(60):
    try:
        with socket.create_connection((host, port), timeout=2):
            print("Postgres is up.")
            break
    except OSError:
        time.sleep(1)
else:
    raise SystemExit("Postgres did not become available in time")
PY
}

role="${1:-web}"

case "$role" in
  web)
    wait_for_db
    python manage.py migrate --noinput
    python manage.py collectstatic --noinput
    echo "Starting web server (gunicorn/WSGI)..."
    exec gunicorn config.wsgi:application \
        --bind 0.0.0.0:8000 --workers 3 --threads 4 --timeout 60
    ;;
  worker)
    wait_for_db
    exec celery -A config worker -l info -Q ingest,pipeline,ocr,notifications,dead_letter
    ;;
  beat)
    wait_for_db
    exec celery -A config beat -l info
    ;;
  *)
    exec "$@"
    ;;
esac
