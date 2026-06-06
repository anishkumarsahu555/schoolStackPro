from django import template

from managementApp.access_control import has_management_permission

register = template.Library()


@register.simple_tag(takes_context=True)
def can_manage(context, module_key, action='view'):
    request = context.get('request')
    user = getattr(request, 'user', None)
    if not user:
        return False
    return has_management_permission(user, module_key, action)
