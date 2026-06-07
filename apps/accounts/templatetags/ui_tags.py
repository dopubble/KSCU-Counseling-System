"""공통 UI 템플릿 태그 (브레드크럼·페이지 헤더)."""

from __future__ import annotations

from django import template
from django.urls import NoReverseMatch, reverse

register = template.Library()

MAX_CRUMBS = 5


def _build_trail(kwargs: dict) -> list[dict]:
    trail: list[dict] = []
    for index in range(1, MAX_CRUMBS + 1):
        label = kwargs.get(f"c{index}_label")
        if not label:
            break
        url_name = kwargs.get(f"c{index}_url_name") or ""
        url_arg = kwargs.get(f"c{index}_url_arg") or ""
        icon = kwargs.get(f"c{index}_icon") or ""
        url = None
        if url_name:
            try:
                if url_arg:
                    url = reverse(url_name, args=[url_arg])
                else:
                    url = reverse(url_name)
            except (NoReverseMatch, TypeError, ValueError):
                url = None
        trail.append(
            {
                "label": label,
                "url": url,
                "icon": icon or None,
            }
        )
    return trail


@register.inclusion_tag("includes/page_breadcrumb.html", takes_context=False)
def page_breadcrumb(variant: str = "", **kwargs):
    """브레드크럼만 렌더 (c1_label, c1_url_name, c1_url_arg, c1_icon …)."""
    return {
        "breadcrumb_trail": _build_trail(kwargs),
        "variant": variant,
    }


@register.inclusion_tag("includes/page_header.html", takes_context=False)
def page_header(
    title: str = "",
    subtitle: str = "",
    heading_tag: str = "h2",
    title_class: str = "site-page-title",
    header_class: str = "site-page-header mb-4",
    show_heading: bool = True,
    variant: str = "",
    **kwargs,
):
    """브레드크럼 + 제목 영역 (mt-4 간격 포함)."""
    return {
        "breadcrumb_trail": _build_trail(kwargs),
        "page_title": title,
        "page_subtitle": subtitle,
        "heading_tag": heading_tag,
        "title_class": title_class,
        "header_class": header_class,
        "show_heading": show_heading,
        "variant": variant,
    }
