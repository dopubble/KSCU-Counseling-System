from django import template

from apps.counseling.presentation_board import (
    format_presentation_comment_content,
    requires_presentation_file_password,
)

register = template.Library()


@register.simple_tag(takes_context=True)
def presentation_file_password_required(context, file_author_id):
    user = context["request"].user
    return requires_presentation_file_password(user, file_author_id)


@register.filter(name="format_presentation_comment")
def format_presentation_comment(value):
    return format_presentation_comment_content(value or "")
