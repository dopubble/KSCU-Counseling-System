from django import template

from apps.counseling.presentation_board import format_presentation_comment_content

register = template.Library()


@register.filter(name="format_presentation_comment")
def format_presentation_comment(value):
    return format_presentation_comment_content(value or "")
