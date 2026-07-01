#!/usr/bin/env sh
# Railway preDeploy — migrate는 필수, Zoom/1회기 복구는 실패해도 배포는 진행
set -eu

python manage.py migrate --noinput

# 운영 수정: 김장서율 삭제 등 (매 배포 idempotent)
python manage.py ops_production_fixup --apply --continue-on-error

# 매칭 대기 테스트 내담자 삭제 (보조)
python manage.py purge_client_accounts --apply --ignore-missing || true

# Zoom join URL 일괄 sync — 배포 시 자동 실행 금지 (링크·Case URL 덮어쓰기 위험)
# 필요 시 수동: python manage.py sync_zoom_join_urls --dry-run 후 실행
# python manage.py sync_zoom_join_urls || true

# 1회기 데이터 복구 — 개별 오류는 명령 내부에서 처리, 배포 중단 방지
python manage.py repair_session1_confirmations --apply --continue-on-error
