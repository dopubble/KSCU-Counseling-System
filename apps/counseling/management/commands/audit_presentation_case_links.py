"""사례발표 게시글의 담당 사례 연결 현황을 읽기 전용으로 집계한다.

운영 DB를 수정하지 않는다. 게시글 본문·개인 상세는 출력하지 않는다.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db.models import Count, Q

from apps.counseling.models import CasePresentationPost


class Command(BaseCommand):
    help = (
        "CasePresentationPost의 case 연결 현황을 숫자만 출력합니다. "
        "UPDATE/DELETE를 하지 않는 읽기 전용 진단 명령입니다."
    )

    def handle(self, *args, **options):
        total = CasePresentationPost.objects.count()
        linked = CasePresentationPost.objects.exclude(case_id=None).count()
        unlinked = CasePresentationPost.objects.filter(case_id=None).count()

        self.stdout.write(f"posts_total={total}")
        self.stdout.write(f"posts_case_linked={linked}")
        self.stdout.write(f"posts_case_null={unlinked}")

        by_author = (
            CasePresentationPost.objects.values("author_id")
            .annotate(
                posts=Count("pk"),
                linked_posts=Count("pk", filter=~Q(case_id=None)),
                null_posts=Count("pk", filter=Q(case_id=None)),
                linked_cases=Count("case_id", distinct=True, filter=~Q(case_id=None)),
            )
            .order_by("-posts")
        )
        self.stdout.write("by_author_id:")
        for row in by_author:
            self.stdout.write(
                "  "
                f"author_id={row['author_id']} "
                f"posts={row['posts']} "
                f"linked_posts={row['linked_posts']} "
                f"null_posts={row['null_posts']} "
                f"distinct_linked_cases={row['linked_cases']}"
            )

        self.stdout.write(
            self.style.NOTICE(
                "사례보고서 실적은 linked distinct Case + case=null 게시글 수로 집계됩니다. "
                "이 명령은 데이터를 변경하지 않습니다."
            )
        )
