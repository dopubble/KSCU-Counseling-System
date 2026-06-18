#!/usr/bin/env sh
# Railway preDeploy — migrate는 필수, Zoom/1회기 복구는 실패해도 배포는 진행
set -eu

python manage.py migrate --noinput

# Zoom scope 오류 등으로 실패해도 Web 서비스 배포는 계속
python manage.py sync_zoom_join_urls || true

# 1회기 데이터 복구 — 개별 오류는 명령 내부에서 처리, 배포 중단 방지
python manage.py repair_session1_confirmations --apply --continue-on-error
