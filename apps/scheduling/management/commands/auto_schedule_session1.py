"""내담자 희망 시간 × 상담사 가용시간 — 1회기 자동 매칭·예약."""

from django.core.management.base import BaseCommand

from apps.scheduling.auto_schedule_session1 import apply_session1_schedule


class Command(BaseCommand):
    help = (
        "내담자 상담 가능 시간과 상담사 가용시간을 비교해 1회기 예약을 제안·확정합니다.\n"
        "데이터: apps/scheduling/client_preference_seed.py\n\n"
        "예시:\n"
        "  python manage.py auto_schedule_session1\n"
        "  python manage.py auto_schedule_session1 --apply --allow-local\n"
        "  python manage.py auto_schedule_session1 --apply --with-zoom"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="DB에 PENDING 생성 후 확정 (기본: 분석만)",
        )
        parser.add_argument(
            "--with-zoom",
            action="store_true",
            help="확정 시 Zoom 회의 생성 (대량 실행 시 API·동시 호스트 제한 주의)",
        )
        parser.add_argument(
            "--weeks",
            type=int,
            default=8,
            help="검색 기간(주, 기본 8)",
        )
        parser.add_argument(
            "--allow-local",
            action="store_true",
            help="로컬 SQLite에서도 실행",
        )
        parser.add_argument(
            "--global-zoom-limit",
            action="store_true",
            help="Zoom 동시 1회 — 전역 겹침 없음 + 같은 날 시작 2시간 간격으로 10명 일괄 재매칭",
        )
        parser.add_argument(
            "--replace-existing",
            action="store_true",
            help="대상 내담자 기존 1회기 예약 삭제 후 재배정 (--apply 필요)",
        )

    def handle(self, *args, **options):
        dry_run = not options["apply"]
        if options["replace_existing"] and dry_run:
            self.stdout.write(
                self.style.WARNING(
                    "[dry-run] --replace-existing: 실제 삭제는 --apply 시에만 수행됩니다."
                )
            )
        results, summary = apply_session1_schedule(
            dry_run=dry_run,
            with_zoom=options["with_zoom"],
            weeks=options["weeks"],
            global_zoom_limit=options["global_zoom_limit"],
            replace_existing=options["replace_existing"],
        )

        matched_rows = [r for r in results if r.status == "matched"]

        if dry_run:
            title = "=== [분석 모드] 예약 확정 가능 내담자"
            if options["global_zoom_limit"]:
                title += " (Zoom 전역 비겹침·동일일 2시간 간격)"
            self.stdout.write(self.style.NOTICE(title + " ==="))
        else:
            self.stdout.write(self.style.SUCCESS("=== 1회기 예약 처리 결과 ==="))

        self.stdout.write(f"{'내담자':<8} {'상담사':<8} {'확정(예정) 일시':<22} {'비고'}")
        self.stdout.write("-" * 70)

        for row in sorted(
            results,
            key=lambda r: (
                r.status != "matched",
                r.scheduled_at.isoformat() if r.scheduled_at else "",
            ),
        ):
            if row.status == "matched" and row.scheduled_at:
                dt_label = row.scheduled_at.strftime("%Y-%m-%d %H:%M")
                self.stdout.write(
                    self.style.SUCCESS(
                        f"{row.client_name:<8} {row.counselor_name:<8} {dt_label:<22}"
                    )
                )
            elif row.status == "already_confirmed" and row.scheduled_at:
                dt_label = row.scheduled_at.strftime("%Y-%m-%d %H:%M")
                self.stdout.write(
                    f"{row.client_name:<8} {row.counselor_name:<8} {dt_label:<22} (기존 확정)"
                )

        self.stdout.write("")
        self.stdout.write(self.style.NOTICE("=== 매칭 불가 / 제외 ==="))
        for row in results:
            if row.status in ("no_overlap", "skipped", "error"):
                self.stdout.write(
                    f"  {row.client_name} → {row.counselor_name}: {row.detail or row.status}"
                )

        prefix = "[dry-run] " if dry_run else ""
        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}삭제 {summary.cleared}건, "
                f"매칭 {summary.matched}건, "
                f"확정 {summary.confirmed}건, "
                f"겹침없음 {summary.no_overlap}건, "
                f"제외·스킵 {summary.skipped}건, "
                f"오류 {summary.errors}건"
            )
        )
        if dry_run and matched_rows:
            hint = (
                "python manage.py auto_schedule_session1 --apply "
                "--global-zoom-limit --replace-existing"
            )
            if options["with_zoom"]:
                hint += " --with-zoom"
            self.stdout.write(self.style.WARNING(f"실제 DB 반영: {hint}"))
