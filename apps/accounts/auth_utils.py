"""로그인/회원가입 후 안전한 리다이렉트(next) 처리."""

from django.utils.http import url_has_allowed_host_and_scheme


def get_safe_next_url(request, *, default: str | None = None) -> str | None:
    """GET/POST의 next가 허용된 호스트일 때만 반환."""
    candidate = request.POST.get("next") or request.GET.get("next")
    if not candidate:
        return default
    if url_has_allowed_host_and_scheme(
        url=candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return default
