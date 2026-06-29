from .models import MessageReport
from .services import room_cards_for_user, unread_count_for_user


def chat_summary(request):
    if not getattr(request, 'user', None) or not request.user.is_authenticated:
        return {}
    from managementApp.access_control import is_owner_or_admin

    recent_rooms = []
    unread_count = unread_count_for_user(request.user)
    if (request.path or '').startswith('/chat/'):
        recent_rooms = room_cards_for_user(request.user, limit=5)
        unread_count = sum(card['unread_count'] for card in recent_rooms)
    is_chat_admin = is_owner_or_admin(request.user)
    return {
        'chat_unread_count': unread_count,
        'chat_recent_rooms': recent_rooms,
        'chat_open_report_count': MessageReport.objects.filter(status=MessageReport.STATUS_OPEN).count() if is_chat_admin else 0,
    }
