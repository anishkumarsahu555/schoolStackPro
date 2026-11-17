from django.utils.html import escape
from django.utils.safestring import mark_safe


def render_lookup_badge(
    obj,
    label=None,
    icon='user outline icon',
    variant='brand',
    lookup_context='audit',
):
    if not obj or not getattr(obj, 'pk', None):
        return escape(label or 'N/A')
    text = label if label not in (None, '') else 'N/A'
    if text == 'N/A':
        return escape(text)
    if getattr(obj, 'isDeleted', False):
        return escape(text)
    meta = obj._meta
    context = lookup_context if lookup_context in {'audit', 'reference'} else 'reference'
    return mark_safe(
        f'<button type="button" class="app-status-badge app-status-badge--{escape(variant)} app-status-badge--button" '
        f'onclick="showLookupDetail(\'{escape(meta.app_label)}\', \'{escape(meta.model_name)}\', \'{escape(obj.pk)}\', \'{escape(context)}\')">'
        f'<i class="{escape(icon)}"></i><span>{escape(text)}</span></button>'
    )
