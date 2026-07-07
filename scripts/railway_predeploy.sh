#!/usr/bin/env sh
# Railway preDeploy — 코드 배포 시 DB 스키마만 반영. 운영 데이터·Zoom·계정은 건드리지 않음.
set -eu

python manage.py check_deploy_safety
python manage.py migrate --noinput
