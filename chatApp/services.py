from homeApp.models import SchoolOwner
from managementApp.models import Student, TeacherDetail
from django.db.models import Count, F, Q

from .models import ChatParticipant, ChatRoom


def display_name(user):
    if not user:
        return 'System'
    student = Student.objects.filter(userID_id=user.id, isDeleted=False).order_by('-datetime').first()
    if student and student.name:
        return student.name
    teacher = TeacherDetail.objects.filter(userID_id=user.id, isDeleted=False).order_by('-datetime').first()
    if teacher and teacher.name:
        return teacher.name
    owner = SchoolOwner.objects.filter(userID_id=user.id, isDeleted=False).order_by('-datetime').first()
    if owner and owner.name:
        return owner.name
    return user.get_full_name() or user.username


def rooms_for_user(user):
    if not user or not user.is_authenticated:
        return ChatRoom.objects.none()
    return ChatRoom.objects.filter(
        participants__userID=user,
        isActive=True,
    ).select_related('schoolID', 'sessionID', 'standardID', 'subjectID').distinct().order_by('-lastUpdatedOn')


def room_cards_for_user(user, limit=None):
    cards = []
    qs = rooms_for_user(user)
    if limit:
        qs = qs[:limit]
    for room in qs:
        participant = room.participants.filter(userID=user).first()
        last_message = room.messages.filter(isDeleted=False).select_related('senderID').order_by('-datetime').first()
        unread_qs = room.messages.filter(isDeleted=False).exclude(senderID=user)
        if participant and participant.lastReadMessage_id:
            unread_qs = unread_qs.filter(id__gt=participant.lastReadMessage_id)
        elif participant and participant.lastReadAt:
            unread_qs = unread_qs.filter(datetime__gt=participant.lastReadAt)
        cards.append({
            'room': room,
            'participant': participant,
            'last_message': last_message,
            'last_sender': display_name(last_message.senderID) if last_message else '',
            'unread_count': unread_qs.count(),
        })
    return cards


def unread_count_for_user(user):
    if not user or not user.is_authenticated:
        return 0
    unread_filter = (
        Q(roomID__messages__isDeleted=False)
        & ~Q(roomID__messages__senderID=user)
        & (
            Q(lastReadMessage_id__isnull=False, roomID__messages__id__gt=F('lastReadMessage_id'))
            | Q(lastReadMessage_id__isnull=True, lastReadAt__isnull=False, roomID__messages__datetime__gt=F('lastReadAt'))
            | Q(lastReadMessage_id__isnull=True, lastReadAt__isnull=True)
        )
    )
    row = ChatParticipant.objects.filter(
        userID=user,
        roomID__isActive=True,
    ).aggregate(total=Count('roomID__messages', filter=unread_filter))
    return row.get('total') or 0
