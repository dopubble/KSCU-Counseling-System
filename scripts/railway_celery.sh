#!/usr/bin/env sh
set -eu

# Railway Celery Worker 서비스 Start Command
exec celery -A kscu_counseling worker -l info
