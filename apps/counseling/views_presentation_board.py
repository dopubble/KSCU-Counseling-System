"""기수 사례발표 게시판 뷰."""

from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.decorators import counselor_required
from apps.accounts.models import CounselorProfile
from apps.counseling.cohort_journal_service import get_counselor_cohort
from apps.counseling.forms import PresentationBoardCommentForm, PresentationBoardPostForm
from apps.counseling.models import CasePresentationComment, CasePresentationPost
from apps.counseling.presentation_board import (
    PRESENTATION_FORM_TEMPLATES,
    get_presentation_form_path,
    require_presentation_board_access,
    resolve_viewer_cohort,
    user_can_comment_on_presentation_post,
    user_can_create_presentation_post,
    user_can_delete_presentation_comment,
    user_can_delete_presentation_post,
    user_is_platform_staff,
)


def _parse_cohort_param(raw) -> int | None:
    if raw in (None, ""):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _board_cohort_or_403(request) -> int:
    requested = _parse_cohort_param(request.GET.get("cohort") or request.POST.get("cohort"))
    cohort = resolve_viewer_cohort(request.user, requested_cohort=requested)
    if cohort is None:
        if user_is_platform_staff(request.user) and requested:
            cohort = requested
        elif user_is_platform_staff(request.user):
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


def _presentation_posts_for_cohort(cohort: int):
    return (
        CasePresentationPost.objects.filter(cohort=cohort)
        .select_related("author")
        .annotate(comment_count=Count("comments"))
        .order_by("-created_at")
    )


def _get_presentation_post_or_404(post_pk):
    return get_object_or_404(
        CasePresentationPost.objects.select_related("author").annotate(
            comment_count=Count("comments")
        ),
        pk=post_pk,
    )


@counselor_required
@require_GET
def presentation_board(request):
    cohort = _board_cohort_or_403(request)
    posts = list(_presentation_posts_for_cohort(cohort))
    viewer_cohort = get_counselor_cohort(request.user)
    return render(
        request,
        "counselor/presentation_board.html",
        {
            "cohort": cohort,
            "viewer_cohort": viewer_cohort,
            "posts": posts,
            "post_form": PresentationBoardPostForm(),
            "form_templates": PRESENTATION_FORM_TEMPLATES,
            "can_create_post": user_can_create_presentation_post(request.user, cohort),
            "is_staff_viewer": user_is_platform_staff(request.user),
        },
    )


@counselor_required
@require_GET
def presentation_board_detail(request, post_pk):
    post = _get_presentation_post_or_404(post_pk)
    require_presentation_board_access(request.user, post.cohort)
    comments = list(
        post.comments.select_related("author").order_by("created_at")
    )
    can_comment = user_can_comment_on_presentation_post(request.user, post)
    return render(
        request,
        "counselor/presentation_board_detail.html",
        {
            "cohort": post.cohort,
            "post": post,
            "comments": comments,
            "comment_form": PresentationBoardCommentForm(),
            "can_comment": can_comment,
            "can_delete_post": user_can_delete_presentation_post(request.user, post),
            "is_staff_viewer": user_is_platform_staff(request.user),
            "is_author": post.author_id == request.user.pk,
            "page_subtitle": f"{post.cohort}기 · {post.author.name}",
        },
    )


@counselor_required
@require_POST
def presentation_board_post_create(request):
    cohort = _board_cohort_or_403(request)
    if not user_can_create_presentation_post(request.user, cohort):
        raise PermissionDenied("게시글 작성 권한이 없습니다.")

    form = PresentationBoardPostForm(request.POST, request.FILES)
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


@counselor_required
@require_GET
def presentation_board_post_file(request, post_pk):
    post = get_object_or_404(CasePresentationPost, pk=post_pk)
    require_presentation_board_access(request.user, post.cohort)
    if not post.file:
        raise Http404("File not found")
    return FileResponse(
        post.file.open("rb"),
        as_attachment=True,
        filename=post.filename,
    )


@counselor_required
@require_GET
def presentation_board_comment_file(request, comment_pk):
    comment = get_object_or_404(
        CasePresentationComment.objects.select_related("post"),
        pk=comment_pk,
    )
    require_presentation_board_access(request.user, comment.post.cohort)
    if not comment.file:
        raise Http404("File not found")
    return FileResponse(
        comment.file.open("rb"),
        as_attachment=True,
        filename=comment.filename,
    )


@counselor_required
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
