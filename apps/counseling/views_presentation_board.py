"""기수 사례발표 게시판 뷰."""

from __future__ import annotations

import logging
import uuid

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts.decorators import counselor_required, presentation_board_viewer_required
from apps.accounts.models import CounselorProfile
from apps.counseling.cohort_journal_service import get_counselor_cohort
from apps.counseling.forms import (
    PresentationBoardCommentEditForm,
    PresentationBoardCommentForm,
    PresentationBoardPostForm,
)
from apps.counseling.models import CasePresentationComment, CasePresentationPost
from apps.counseling.presentation_board import (
    PRESENTATION_BULK_ZIP_PASSWORD_NOTICE,
    PRESENTATION_FILE_PASSWORD_MIN_LENGTH,
    PRESENTATION_FILE_PASSWORD_NOTICE,
    PRESENTATION_FORM_TEMPLATES,
    count_presentation_comment_peers,
    get_presentation_form_path,
    presentation_board_cohort_options,
    presentation_comment_file_content_type,
    require_presentation_board_access,
    resolve_viewer_cohort,
    user_can_browse_all_presentation_cohorts,
    user_can_comment_on_presentation_post,
    user_can_create_presentation_post,
    user_can_delete_presentation_comment,
    user_can_delete_presentation_post,
    user_can_edit_presentation_comment,
    user_is_platform_staff,
    user_is_supervisor_viewer,
)
from apps.counseling.presentation_bulk_download import (
    bulk_zip_download_filename,
    build_presentation_posts_zip,
)
from apps.counseling.presentation_pdf_encrypt import encrypt_pdf_bytes, read_uploaded_file_bytes

logger = logging.getLogger(__name__)


def _parse_cohort_param(raw) -> int | None:
    if raw in (None, ""):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _resolve_list_cohort_filter(request) -> int | None:
    """목록 필터 기수. 수퍼바이저·관리자는 None이면 전 기수."""
    requested = _parse_cohort_param(request.GET.get("cohort") or request.POST.get("cohort"))
    user = request.user

    if user_can_browse_all_presentation_cohorts(user):
        if requested is not None:
            require_presentation_board_access(user, requested)
        return requested

    cohort = resolve_viewer_cohort(user, requested_cohort=requested)
    if cohort is None:
        raise PermissionDenied("기수 정보가 없어 사례발표 게시판을 이용할 수 없습니다.")
    require_presentation_board_access(user, cohort)
    return cohort


def _board_cohort_or_403(request) -> int:
    requested = _parse_cohort_param(request.GET.get("cohort") or request.POST.get("cohort"))
    cohort = resolve_viewer_cohort(request.user, requested_cohort=requested)
    if cohort is None:
        if user_can_browse_all_presentation_cohorts(request.user) and requested:
            cohort = requested
        elif user_can_browse_all_presentation_cohorts(request.user):
            cohort = (
                CounselorProfile.objects.exclude(cohort__isnull=True)
                .order_by("-cohort")
                .values_list("cohort", flat=True)
                .first()
            )
    if cohort is None:
        raise PermissionDenied("기수 정보가 없어 사례발표 게시판을 이용할 수 없습니다.")
    require_presentation_board_access(request.user, cohort)
    return cohort


def _presentation_posts_query(cohort: int | None):
    qs = (
        CasePresentationPost.objects.select_related("author")
        .annotate(comment_count=Count("comments"))
    )
    if cohort is not None:
        qs = qs.filter(cohort=cohort)
    return qs.order_by("-cohort", "-created_at")


def _presentation_board_list_url(*, cohort: int | None) -> str:
    base = reverse("counselor:presentation_board")
    if cohort is not None:
        return f"{base}?cohort={cohort}"
    return base


def _get_presentation_post_or_404(post_pk):
    return get_object_or_404(
        CasePresentationPost.objects.select_related("author").annotate(
            comment_count=Count("comments")
        ),
        pk=post_pk,
    )


def _redirect_after_file_download_failure(request, *, fallback_url: str):
    next_url = (request.POST.get("next") or "").strip()
    if next_url.startswith("/"):
        return redirect(next_url)
    return redirect(fallback_url)


def _presentation_download_error_response(request, message: str, *, fallback_url: str):
    """일괄 ZIP fetch 다운로드 — XHR이면 본문 오류, 아니면 리다이렉트."""
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return HttpResponse(message, status=400, content_type="text/plain; charset=utf-8")
    messages.error(request, message)
    return _redirect_after_file_download_failure(request, fallback_url=fallback_url)


def _encrypted_pdf_download_response(*, pdf_bytes: bytes, filename: str) -> HttpResponse:
    download_name = filename if filename.lower().endswith(".pdf") else f"{filename}.pdf"
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = content_disposition_header(
        as_attachment=True,
        filename=download_name,
    )
    response["Content-Length"] = len(pdf_bytes)
    return response


def _zip_download_response(*, zip_bytes: bytes, filename: str) -> HttpResponse:
    response = HttpResponse(zip_bytes, content_type="application/zip")
    response["Content-Disposition"] = content_disposition_header(
        as_attachment=True,
        filename=filename,
    )
    response["Content-Length"] = len(zip_bytes)
    return response


def _serve_presentation_file(
    request,
    *,
    file_field,
    storage_name: str,
    filename: str,
    fallback_url: str,
):
    """게시글 PDF — POST + 암호 입력 후 암호화 다운로드."""
    if not file_field or not storage_name:
        raise Http404("File not found")

    if request.method == "GET":
        raise PermissionDenied("암호 설정 후 다운로드할 수 있습니다.")

    password = (request.POST.get("file_password") or "").strip()
    if len(password) < PRESENTATION_FILE_PASSWORD_MIN_LENGTH:
        messages.error(
            request,
            f"파일 암호는 {PRESENTATION_FILE_PASSWORD_MIN_LENGTH}자 이상 입력해 주세요.",
        )
        return _redirect_after_file_download_failure(request, fallback_url=fallback_url)

    try:
        raw_bytes = read_uploaded_file_bytes(file_field)
        encrypted = encrypt_pdf_bytes(raw_bytes, password)
        return _encrypted_pdf_download_response(pdf_bytes=encrypted, filename=filename)
    except FileNotFoundError:
        messages.error(
            request,
            "첨부 파일을 찾을 수 없습니다. 파일이 삭제되었거나 서버 저장소에 없을 수 있습니다.",
        )
        return _redirect_after_file_download_failure(request, fallback_url=fallback_url)
    except Exception:
        logger.exception("Presentation encrypted PDF download failed filename=%s", filename)
        messages.error(
            request,
            "파일을 준비하는 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
        )
        return _redirect_after_file_download_failure(request, fallback_url=fallback_url)


def _serve_presentation_comment_file(
    *,
    file_field,
    storage_name: str,
    filename: str,
):
    """댓글 첨부 파일 — 암호 없이 원본 그대로 다운로드."""
    if not file_field or not storage_name:
        raise Http404("File not found")
    try:
        download_name = filename or "attachment"
        response = FileResponse(
            file_field.open("rb"),
            as_attachment=True,
            filename=download_name,
        )
        response["Content-Type"] = presentation_comment_file_content_type(download_name)
        return response
    except FileNotFoundError:
        raise Http404("File not found") from None


def _parse_post_ids(raw_ids) -> list[uuid.UUID]:
    parsed: list[uuid.UUID] = []
    for raw in raw_ids:
        try:
            parsed.append(uuid.UUID(str(raw)))
        except (TypeError, ValueError, AttributeError):
            continue
    return parsed


@presentation_board_viewer_required
@require_GET
def presentation_board(request):
    cohort_filter = _resolve_list_cohort_filter(request)
    posts = list(_presentation_posts_query(cohort_filter))
    viewer_cohort = get_counselor_cohort(request.user)
    can_filter = user_can_browse_all_presentation_cohorts(request.user)
    if cohort_filter is not None:
        page_subtitle = f"{cohort_filter}기 동기 수퍼비전 · 사례발표 자료 공유"
        list_heading = f"{cohort_filter}기 게시글"
    elif can_filter:
        page_subtitle = "전 기수 사례발표 보고서 · 수퍼비전 자료 열람"
        list_heading = "전체 게시글"
    else:
        page_subtitle = f"{cohort_filter}기 동기 수퍼비전 · 사례발표 자료 공유"
        list_heading = f"{cohort_filter}기 게시글"

    return render(
        request,
        "counselor/presentation_board.html",
        {
            "cohort": cohort_filter,
            "viewer_cohort": viewer_cohort,
            "posts": posts,
            "post_form": PresentationBoardPostForm(author_name=request.user.name),
            "form_templates": PRESENTATION_FORM_TEMPLATES,
            "can_create_post": (
                cohort_filter is not None
                and user_can_create_presentation_post(request.user, cohort_filter)
            ),
            "is_staff_viewer": user_is_platform_staff(request.user),
            "is_supervisor_viewer": user_is_supervisor_viewer(request.user),
            "can_filter_cohorts": can_filter,
            "cohort_options": presentation_board_cohort_options() if can_filter else [],
            "page_subtitle": page_subtitle,
            "list_heading": list_heading,
            "show_cohort_column": can_filter and cohort_filter is None,
            "bulk_zip_password_notice": PRESENTATION_BULK_ZIP_PASSWORD_NOTICE,
            "bulk_download_url": reverse("counselor:presentation_board_bulk_download"),
        },
    )


@presentation_board_viewer_required
@require_GET
def presentation_board_detail(request, post_pk):
    post = _get_presentation_post_or_404(post_pk)
    require_presentation_board_access(request.user, post.cohort)
    comments = list(
        post.comments.select_related("author").order_by("created_at")
    )
    comment_count = len(comments)
    comment_peer_total = count_presentation_comment_peers(
        post.cohort,
        exclude_author_id=post.author_id,
    )
    comment_participation_pct = (
        round(100 * comment_count / comment_peer_total) if comment_peer_total else 0
    )
    can_comment = user_can_comment_on_presentation_post(request.user, post)
    cohort_filter = _parse_cohort_param(request.GET.get("cohort"))
    return render(
        request,
        "counselor/presentation_board_detail.html",
        {
            "cohort": post.cohort,
            "cohort_filter": cohort_filter,
            "post": post,
            "comments": comments,
            "comment_count": comment_count,
            "comment_peer_total": comment_peer_total,
            "comment_participation_pct": comment_participation_pct,
            "comment_form": PresentationBoardCommentForm(),
            "can_comment": can_comment,
            "can_delete_post": user_can_delete_presentation_post(request.user, post),
            "is_staff_viewer": user_is_platform_staff(request.user),
            "is_supervisor_viewer": user_is_supervisor_viewer(request.user),
            "is_author": post.author_id == request.user.pk,
            "page_subtitle": f"{post.cohort}기 · {post.author.name}",
            "file_password_notice": PRESENTATION_FILE_PASSWORD_NOTICE,
            "post_file_download_url": reverse(
                "counselor:presentation_board_post_file",
                kwargs={"post_pk": post.pk},
            ),
            "board_list_url": _presentation_board_list_url(cohort=cohort_filter),
        },
    )


@counselor_required
@require_POST
def presentation_board_post_create(request):
    cohort = _board_cohort_or_403(request)
    if not user_can_create_presentation_post(request.user, cohort):
        raise PermissionDenied("게시글 작성 권한이 없습니다.")

    form = PresentationBoardPostForm(
        request.POST,
        request.FILES,
        author_name=request.user.name,
    )
    if not form.is_valid():
        for errors in form.errors.values():
            if errors:
                messages.error(request, errors[0])
                break
        return redirect("counselor:presentation_board")

    post = CasePresentationPost.objects.create(
        cohort=cohort,
        author=request.user,
        title=form.cleaned_data["title"].strip(),
        content=(form.cleaned_data.get("content") or "").strip(),
        file=form.cleaned_data["file"],
    )
    messages.success(request, "사례발표 보고서가 등록되었습니다.")
    return redirect("counselor:presentation_board_detail", post_pk=post.pk)


@counselor_required
@require_POST
def presentation_board_post_delete(request, post_pk):
    post = get_object_or_404(CasePresentationPost, pk=post_pk)
    require_presentation_board_access(request.user, post.cohort)
    if not user_can_delete_presentation_post(request.user, post):
        raise PermissionDenied("삭제 권한이 없습니다.")
    if post.file:
        post.file.delete(save=False)
    for comment in post.comments.all():
        if comment.file:
            comment.file.delete(save=False)
    post.delete()
    messages.success(request, "게시글이 삭제되었습니다.")
    return redirect("counselor:presentation_board")


@counselor_required
@require_POST
def presentation_board_comment_create(request, post_pk):
    post = get_object_or_404(CasePresentationPost, pk=post_pk)
    require_presentation_board_access(request.user, post.cohort)
    if not user_can_comment_on_presentation_post(request.user, post):
        raise PermissionDenied("이 게시글에는 사례개념화 댓글을 달 수 없습니다.")

    form = PresentationBoardCommentForm(request.POST, request.FILES)
    if not form.is_valid():
        for errors in form.errors.values():
            if errors:
                messages.error(request, errors[0])
                break
        return redirect("counselor:presentation_board_detail", post_pk=post.pk)

    CasePresentationComment.objects.create(
        post=post,
        author=request.user,
        content=(form.cleaned_data.get("content") or "").strip(),
        file=form.cleaned_data.get("file"),
    )
    messages.success(request, "사례개념화보고서 댓글이 등록되었습니다.")
    return redirect("counselor:presentation_board_detail", post_pk=post.pk)


@counselor_required
@require_POST
def presentation_board_comment_edit(request, comment_pk):
    comment = get_object_or_404(
        CasePresentationComment.objects.select_related("post"),
        pk=comment_pk,
    )
    require_presentation_board_access(request.user, comment.post.cohort)
    if not user_can_edit_presentation_comment(request.user, comment):
        raise PermissionDenied("수정 권한이 없습니다.")

    form = PresentationBoardCommentEditForm(
        request.POST,
        request.FILES,
        has_existing_file=bool(comment.file),
    )
    if not form.is_valid():
        for errors in form.errors.values():
            if errors:
                messages.error(request, errors[0])
                break
        return redirect("counselor:presentation_board_detail", post_pk=comment.post_id)

    comment.content = form.cleaned_data["content"]
    new_file = form.cleaned_data.get("file")
    remove_file = form.cleaned_data.get("remove_file", False)
    if new_file:
        if comment.file:
            comment.file.delete(save=False)
        comment.file = new_file
    elif remove_file and comment.file:
        comment.file.delete(save=False)
        comment.file = ""
    comment.save(update_fields=["content", "file", "updated_at"])
    messages.success(request, "댓글이 수정되었습니다.")
    return redirect("counselor:presentation_board_detail", post_pk=comment.post_id)


@counselor_required
@require_POST
def presentation_board_comment_delete(request, comment_pk):
    comment = get_object_or_404(
        CasePresentationComment.objects.select_related("post"),
        pk=comment_pk,
    )
    require_presentation_board_access(request.user, comment.post.cohort)
    if not user_can_delete_presentation_comment(request.user, comment):
        raise PermissionDenied("삭제 권한이 없습니다.")
    if comment.file:
        comment.file.delete(save=False)
    comment.delete()
    messages.success(request, "댓글이 삭제되었습니다.")
    return redirect("counselor:presentation_board_detail", post_pk=comment.post_id)


@presentation_board_viewer_required
@require_http_methods(["GET", "POST"])
def presentation_board_post_file(request, post_pk):
    post = get_object_or_404(CasePresentationPost, pk=post_pk)
    require_presentation_board_access(request.user, post.cohort)
    fallback_url = reverse(
        "counselor:presentation_board_detail",
        kwargs={"post_pk": post.pk},
    )
    return _serve_presentation_file(
        request,
        file_field=post.file,
        storage_name=post.file.name if post.file else "",
        filename=post.filename,
        fallback_url=fallback_url,
    )


@presentation_board_viewer_required
@require_GET
def presentation_board_comment_file(request, comment_pk):
    comment = get_object_or_404(
        CasePresentationComment.objects.select_related("post"),
        pk=comment_pk,
    )
    require_presentation_board_access(request.user, comment.post.cohort)
    return _serve_presentation_comment_file(
        file_field=comment.file,
        storage_name=comment.file.name if comment.file else "",
        filename=comment.filename,
    )


@presentation_board_viewer_required
@require_POST
def presentation_board_bulk_download(request):
    cohort_filter = _resolve_list_cohort_filter(request)
    fallback_url = _presentation_board_list_url(cohort=cohort_filter)

    password = (request.POST.get("file_password") or "").strip()
    if len(password) < PRESENTATION_FILE_PASSWORD_MIN_LENGTH:
        return _presentation_download_error_response(
            request,
            f"ZIP 암호는 {PRESENTATION_FILE_PASSWORD_MIN_LENGTH}자 이상 입력해 주세요.",
            fallback_url=fallback_url,
        )

    post_ids = _parse_post_ids(request.POST.getlist("post_ids"))
    if not post_ids:
        return _presentation_download_error_response(
            request,
            "다운로드할 게시글을 하나 이상 선택해 주세요.",
            fallback_url=fallback_url,
        )

    posts = list(
        CasePresentationPost.objects.filter(pk__in=post_ids)
        .select_related("author")
        .order_by("-cohort", "-created_at")
    )
    if len(posts) != len(set(post_ids)):
        return _presentation_download_error_response(
            request,
            "선택한 게시글 중 일부를 찾을 수 없습니다.",
            fallback_url=fallback_url,
        )

    for post in posts:
        require_presentation_board_access(request.user, post.cohort)

    try:
        zip_bytes = build_presentation_posts_zip(posts, password)
    except FileNotFoundError as exc:
        return _presentation_download_error_response(
            request,
            str(exc),
            fallback_url=fallback_url,
        )
    except Exception:
        logger.exception("Presentation bulk ZIP download failed post_count=%s", len(posts))
        return _presentation_download_error_response(
            request,
            "ZIP 파일을 준비하는 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
            fallback_url=fallback_url,
        )

    download_name = bulk_zip_download_filename(cohort=cohort_filter)
    return _zip_download_response(zip_bytes=zip_bytes, filename=download_name)


@presentation_board_viewer_required
@require_GET
def presentation_board_form_download(request, template_key: str):
    _board_cohort_or_403(request)
    if template_key not in PRESENTATION_FORM_TEMPLATES:
        raise Http404("양식을 찾을 수 없습니다.")
    path = get_presentation_form_path(template_key)
    return FileResponse(
        path.open("rb"),
        as_attachment=True,
        filename=path.name,
    )
