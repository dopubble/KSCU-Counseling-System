"""예약·상담 시간 공통 상수."""

DEFAULT_APPOINTMENT_DURATION_MINUTES = 50

# 비대면 Zoom 호스트 점유: 예약 종료 후 추가 완충(분) — 연속 타임 동일 호스트 충돌 방지
DEFAULT_ZOOM_HOST_BUFFER_MINUTES = 30

# 예약 캘린더 타임라인 (09:00~22:00, BOOKING_SLOT_INTERVAL_MINUTES 간격 시작)
BOOKING_SLOT_START_HOUR = 9
BOOKING_SLOT_END_HOUR = 22
BOOKING_SLOT_INTERVAL_MINUTES = 30

# 대면 상담실 동시 이용 가능 수 (비대면 Zoom 호스트 수와 별도)
IN_PERSON_ROOM_CAPACITY = 2
