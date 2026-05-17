from .models import MessageReport
from .services import room_cards_for_user, unread_count_for_user


def chat_summary(request):
    if not getattr(request, 'user', None) or not request.user.is_authenticated:
        return {}
    is_chat_admin = request.user.groups.filter(name__in=['Admin', 'Owner']).exists()
    return {
        'chat_unread_count': unread_count_for_user(request.user),
        'chat_recent_rooms': room_cards_for_user(request.user, limit=5),
        'chat_open_report_count': MessageReport.objects.filter(status=MessageReport.STATUS_OPEN).count() if is_chat_admin else 0,
    }
