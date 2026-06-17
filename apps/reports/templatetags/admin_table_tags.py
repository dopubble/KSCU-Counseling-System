from django import template

from apps.reports.table_sort import build_sort_query

register = template.Library()


@register.inclusion_tag("includes/admin_sortable_th.html", takes_context=True)
def admin_sort_th(context, label, field, *, align=""):
    request = context["request"]
    sort_field = context.get("sort_field", "")
    sort_dir = context.get("sort_dir", "asc")
    is_active = sort_field == field
    href = build_sort_query(request, field)
    if is_active:
        icon = "bi-chevron-up" if sort_dir == "asc" else "bi-chevron-down"
    else:
        icon = "bi-chevron-down"
    return {
        "label": label,
        "href": href,
        "is_active": is_active,
        "sort_dir": sort_dir if is_active else "",
        "icon_class": icon,
        "align": align,
    }
