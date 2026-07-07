"""사례발표 게시글 PDF — DB에 남은 경로로 서버 디스크에 파일 복구."""

from __future__ import annotations

import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.counseling.models import CasePresentationPost


class Command(BaseCommand):
    help = (
        "재배포 등으로 사라진 사례발표 게시글 PDF를 기존 DB 경로(MEDIA_ROOT)에 복구합니다.\n"
        "예) python manage.py restore_presentation_post_file --list-missing\n"
        "예) python manage.py restore_presentation_post_file --author-name 신영화 "
        "--source /tmp/shinyounghwa.pdf"
    )

    def add_arguments(self, parser):
        parser.add_argument("--list-missing", action="store_true", help="저장소에 없는 게시글 목록")
        parser.add_argument("--post-pk", help="게시글 UUID")
        parser.add_argument("--author-name", help="작성자 이름")
        parser.add_argument("--source", help="복구할 PDF (서버 로컬 경로)")
        parser.add_argument("--dry-run", action="store_true", help="복사하지 않고 경로만 표시")

    def handle(self, *args, **options):
        if options["list_missing"]:
            self._list_missing()
            return

        source_raw = options.get("source")
        if not source_raw:
            raise CommandError("--source 가 필요합니다. (--list-missing 만 단독 실행 가능)")

        source = Path(source_raw).expanduser().resolve()
        if not source.is_file():
            raise CommandError(f"원본 파일을 찾을 수 없습니다: {source}")

        post = self._resolve_post(
            post_pk=options.get("post_pk"),
            author_name=options.get("author_name"),
        )
        storage_name = (post.file.name or "").strip()
        if not storage_name:
            raise CommandError(f"게시글 {post.pk} 에 저장 경로(file.name)가 없습니다.")

        dest = Path(settings.MEDIA_ROOT) / storage_name
        self.stdout.write(f"post={post.pk} author={post.author.name} title={post.title}")
        self.stdout.write(f"storage={storage_name}")
        self.stdout.write(f"dest={dest}")

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("[dry-run] 복사하지 않았습니다."))
            return

        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)

        if not post.file.storage.exists(storage_name):
            raise CommandError("복사 후에도 storage.exists() 가 False 입니다. MEDIA_ROOT 를 확인하세요.")

        size = dest.stat().st_size
        self.stdout.write(self.style.SUCCESS(f"복구 완료 — {size:,} bytes → {dest}"))

    def _list_missing(self) -> None:
        missing = 0
        for post in CasePresentationPost.objects.select_related("author").order_by(
            "-created_at"
        ):
            name = (post.file.name or "").strip()
            if not name:
                continue
            ok = post.file.storage.exists(name)
            status = "OK" if ok else "MISSING"
            if not ok:
                missing += 1
            self.stdout.write(
                f"{status}\t{post.author.name}\t{post.pk}\t{name}\t{post.title}"
            )
        self.stdout.write(self.style.NOTICE(f"MEDIA_ROOT={settings.MEDIA_ROOT}"))
        self.stdout.write(self.style.WARNING(f"MISSING {missing}건"))

    def _resolve_post(self, *, post_pk: str | None, author_name: str | None):
        if post_pk:
            return CasePresentationPost.objects.select_related("author").get(pk=post_pk)

        if not author_name:
            raise CommandError("--post-pk 또는 --author-name 중 하나가 필요합니다.")

        qs = CasePresentationPost.objects.select_related("author").filter(
            author__name=author_name
        )
        count = qs.count()
        if count == 0:
            raise CommandError(f"작성자 '{author_name}' 게시글을 찾을 수 없습니다.")
        if count > 1:
            lines = [f"  {p.pk}  {p.title}" for p in qs.order_by("-created_at")]
            raise CommandError(
                f"작성자 '{author_name}' 게시글이 {count}건입니다. --post-pk 로 지정하세요.\n"
                + "\n".join(lines)
            )
        return qs.get()
