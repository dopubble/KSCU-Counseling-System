"""관리자 — 사례발표 게시글 case=NULL 레거시 연결."""

from __future__ import annotations

from collections import defaultdict

from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.decorators import role_required
from apps.accounts.models import UserRole
from apps.counseling.forms import AdminUnlinkedPresentationCaseForm
from apps.counseling.models import Case, CasePresentationPost


def _unlinked_posts_queryset():
    return (
        CasePresentationPost.objects.filter(case__isnull=True)
        .select_related("author")
        .order_by("created_at", "pk")
    )


def _cases_by_author_id(author_ids: list) -> dict:
    if not author_ids:
        return {}
    grouped: dict = defaultdict(list)
    cases = (
        Case.objects.filter(counselor_id__in=author_ids)
        .select_related("client", "application")
        .order_by("case_number")
    )
    for case in cases:
        grouped[case.counselor_id].append(case)
    return grouped


def _build_unlinked_rows(posts):
    author_ids = list({post.author_id for post in posts})
    cases_by_author = _cases_by_author_id(author_ids)
    rows = []
    for post in posts:
        author_cases = cases_by_author.get(post.author_id, ())
        form = AdminUnlinkedPresentationCaseForm(
            author=post.author,
            cases=author_cases,
        )
        rows.append(
            {
                "post": post,
                "form": form,
                "case_count": len(author_cases),
            }
        )
    return rows


@role_required(UserRole.ADMIN)
@require_GET
def admin_unlinked_case_presentations(request):
    posts = list(_unlinked_posts_queryset())
    return render(
        request,
        "admin_panel/unlinked_case_presentations.html",
        {
            "rows": _build_unlinked_rows(posts),
            "unlinked_count": len(posts),
        },
    )


@role_required(UserRole.ADMIN)
@require_POST
def admin_unlinked_case_presentation_link(request, post_pk):
    with transaction.atomic():
        post = get_object_or_404(
            CasePresentationPost.objects.select_for_update().select_related("author"),
            pk=post_pk,
        )
        if post.case_id is not None:
            messages.error(request, "이미 사례가 연결된 게시글입니다.")
            return redirect("admin_panel:unlinked_case_presentations")

        form = AdminUnlinkedPresentationCaseForm(request.POST, author=post.author)
        if not form.is_valid():
            for errors in form.errors.values():
                if errors:
                    messages.error(request, errors[0])
                    break
            return redirect("admin_panel:unlinked_case_presentations")

        case = form.cleaned_data["case"]
        if case.counselor_id != post.author_id:
            messages.error(request, "작성자에게 배정된 사례만 연결할 수 있습니다.")
            return redirect("admin_panel:unlinked_case_presentations")

        post.case = case
        post.save(update_fields=["case", "updated_at"])

    messages.success(request, "사례가 연결되었습니다.")
    return redirect("admin_panel:unlinked_case_presentations")
