#!/usr/bin/env sh
set -eu

# Railway Web 서비스 Start Command (대시보드에 붙여넣기용)
# preDeployCommand에서 migrate를 실행하지 않는 경우에만 사용:
#   python manage.py migrate --noinput &&
exec gunicorn kscu_counseling.wsgi:application \
  --bind "0.0.0.0:${PORT:-8000}" \
  --workers 2 \
  --worker-class gthread \
  --threads 4 \
  --timeout 120 \
  --graceful-timeout 30 \
  --keep-alive 5
