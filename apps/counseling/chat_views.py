"""사례 상세 1:1 채팅 API (JSON polling)."""

from __future__ import annotations

import json

from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.auth_utils import skip_session_save
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


def _user_can_access_chat_case(user, case_pk) -> bool:
    """담당 상담사가 배정된 사례에 참여 가능한지 — 단일 EXISTS 쿼리."""
    return (
        Case.objects.filter(pk=case_pk, counselor_id__isnull=False)
        .filter(Q(client_id=user.id) | Q(counselor_id=user.id))
        .exists()
    )


def _require_chat_case_access(request, pk) -> None:
    if not _user_can_access_chat_case(request.user, pk):
        raise PermissionDenied("이 사례의 채팅에 참여할 수 없습니다.")


def _get_chat_case(request, pk) -> Case:
    """메시지 전송 등 Case 객체가 필요한 경우에만 사용."""
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


def _mark_messages_read(message_pks) -> None:
    if not message_pks:
        return
    ChatMessage.objects.filter(pk__in=message_pks, is_read=False).update(is_read=True)


def _chat_unread_response(request, pk):
    _require_chat_case_access(request, pk)
    has_unread = ChatMessage.objects.filter(
        case_id=pk,
        recipient_id=request.user.id,
        is_read=False,
    ).exists()
    return JsonResponse({"has_unread": has_unread})


def _chat_messages_response(request, pk):
    _require_chat_case_access(request, pk)
    qs = (
        ChatMessage.objects.filter(case_id=pk)
        .select_related("sender")
        .order_by("created_at")
    )
    after = (request.GET.get("after") or "").strip()
    if after:
        anchor_created_at = (
            ChatMessage.objects.filter(pk=after, case_id=pk)
            .values_list("created_at", flat=True)
            .first()
        )
        if anchor_created_at is None:
            raise Http404

        qs = qs.filter(created_at__gt=anchor_created_at)

    raw_messages = list(qs[:CHAT_FETCH_LIMIT])
    messages = [_serialize_message(msg, request.user) for msg in raw_messages]

    if raw_messages:
        unread_ids = [
            msg.pk
            for msg in raw_messages
            if msg.recipient_id == request.user.id and not msg.is_read
        ]
        _mark_messages_read(unread_ids)

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
@skip_session_save
@require_GET
def client_case_chat_unread(request, pk):
    return _chat_unread_response(request, pk)


@role_required(UserRole.CLIENT)
@skip_session_save
@require_GET
def client_case_chat_messages(request, pk):
    return _chat_messages_response(request, pk)


@role_required(UserRole.CLIENT)
@require_POST
def client_case_chat_send(request, pk):
    return _chat_send_response(request, pk)


@role_required(UserRole.COUNSELOR)
@skip_session_save
@require_GET
def counselor_case_chat_unread(request, pk):
    return _chat_unread_response(request, pk)


@role_required(UserRole.COUNSELOR)
@skip_session_save
@require_GET
def counselor_case_chat_messages(request, pk):
    return _chat_messages_response(request, pk)


@role_required(UserRole.COUNSELOR)
@require_POST
def counselor_case_chat_send(request, pk):
    return _chat_send_response(request, pk)
