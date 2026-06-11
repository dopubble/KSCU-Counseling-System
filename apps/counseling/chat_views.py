"""사례 상세 1:1 채팅 API (JSON polling)."""

from __future__ import annotations

import json

from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.decorators import role_required
from apps.accounts.models import UserRole

from .models import Case, ChatMessage

MAX_CHAT_BODY = 2000
CHAT_FETCH_LIMIT = 200


def _serialize_message(message: ChatMessage, current_user) -> dict:
    return {
        "id": str(message.pk),
        "body": message.body,
        "sender_id": str(message.sender_id),
        "sender_name": message.sender.name,
        "is_mine": message.sender_id == current_user.id,
        "created_at": message.created_at.isoformat(),
    }


def _get_chat_case(request, pk) -> Case:
    case = get_object_or_404(
        Case.objects.select_related("client", "counselor"),
        pk=pk,
    )
    if not case.counselor_id:
        raise PermissionDenied("담당 상담사가 배정된 후 채팅을 사용할 수 있습니다.")
    if request.user.id not in (case.client_id, case.counselor_id):
        raise PermissionDenied("이 사례의 채팅에 참여할 수 없습니다.")
    return case


def _resolve_recipient(case: Case, sender):
    if sender.id == case.client_id:
        return case.counselor
    if sender.id == case.counselor_id:
        return case.client
    raise PermissionDenied("이 사례의 채팅에 참여할 수 없습니다.")


def _mark_chat_read(case: Case, user) -> None:
    ChatMessage.objects.filter(
        case=case,
        recipient=user,
        is_read=False,
    ).update(is_read=True)


def _chat_unread_response(request, pk):
    case = _get_chat_case(request, pk)
    has_unread = ChatMessage.objects.filter(
        case=case,
        recipient=request.user,
        is_read=False,
    ).exists()
    return JsonResponse({"has_unread": has_unread})


def _chat_messages_response(request, pk):
    case = _get_chat_case(request, pk)
    qs = (
        ChatMessage.objects.filter(case=case)
        .select_related("sender")
        .order_by("created_at")
    )
    after = (request.GET.get("after") or "").strip()
    if after:
        anchor = get_object_or_404(ChatMessage, pk=after, case=case)
        qs = qs.filter(created_at__gt=anchor.created_at)

    messages = [_serialize_message(msg, request.user) for msg in qs[:CHAT_FETCH_LIMIT]]
    _mark_chat_read(case, request.user)
    return JsonResponse({"messages": messages})


def _chat_send_response(request, pk):
    case = _get_chat_case(request, pk)
    try:
        payload = json.loads(request.body.decode() or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "잘못된 요청입니다."}, status=400)

    body = (payload.get("body") or "").strip()
    if not body:
        return JsonResponse({"error": "메시지를 입력해 주세요."}, status=400)
    if len(body) > MAX_CHAT_BODY:
        return JsonResponse(
            {"error": f"메시지는 {MAX_CHAT_BODY}자 이하로 입력해 주세요."},
            status=400,
        )

    recipient = _resolve_recipient(case, request.user)
    message = ChatMessage.objects.create(
        case=case,
        sender=request.user,
        recipient=recipient,
        body=body,
    )
    return JsonResponse(
        {"message": _serialize_message(message, request.user)},
        status=201,
    )


@role_required(UserRole.CLIENT)
@require_GET
def client_case_chat_unread(request, pk):
    return _chat_unread_response(request, pk)


@role_required(UserRole.CLIENT)
@require_GET
def client_case_chat_messages(request, pk):
    return _chat_messages_response(request, pk)


@role_required(UserRole.CLIENT)
@require_POST
def client_case_chat_send(request, pk):
    return _chat_send_response(request, pk)


@role_required(UserRole.COUNSELOR)
@require_GET
def counselor_case_chat_unread(request, pk):
    return _chat_unread_response(request, pk)


@role_required(UserRole.COUNSELOR)
@require_GET
def counselor_case_chat_messages(request, pk):
    return _chat_messages_response(request, pk)


@role_required(UserRole.COUNSELOR)
@require_POST
def counselor_case_chat_send(request, pk):
    return _chat_send_response(request, pk)
