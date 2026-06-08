import csv
import os

from django.contrib import messages
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from homeApp.push_service import send_chat_push_notifications
from homeApp.utils import login_required
from managementApp.access_control import has_management_permission, is_owner_or_admin, user_has_management_access
from managementApp.models import AssignSubjectsToClass, AssignSubjectsToTeacher, Standard, Student, TeacherDetail
from utils.custom_decorators import check_groups

from .models import ChatMessage, ChatParticipant, ChatPinnedMessage, ChatRoom, ChatSavedMessage, MessageReaction, MessageReadReceipt, MessageReport
from .services import display_name, room_cards_for_user, rooms_for_user


MAX_ATTACHMENT_SIZE = 5 * 1024 * 1024
MAX_PINNED_MESSAGES = 5
MUTE_DURATION_OPTIONS = (
    (15, '15 minutes'),
    (60, '1 hour'),
    (360, '6 hours'),
    (1440, '1 day'),
    (10080, '1 week'),
)
NOTIFICATION_MUTE_OPTIONS = (
    ('0', 'Until turned on'),
    ('15', '15 minutes'),
    ('60', '1 hour'),
    ('360', '6 hours'),
    ('today', 'Today'),
)
REACTION_OPTIONS = (
    {'key': MessageReaction.REACTION_ACK, 'label': 'Acknowledged', 'icon': 'check circle outline'},
    {'key': MessageReaction.REACTION_LIKE, 'label': 'Useful', 'icon': 'thumbs up outline'},
    {'key': MessageReaction.REACTION_QUESTION, 'label': 'Question', 'icon': 'question circle outline'},
)
REACTION_KEYS = {item['key'] for item in REACTION_OPTIONS}
ALLOWED_ATTACHMENT_EXTENSIONS = {'.pdf', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.doc', '.docx', '.webm', '.ogg', '.mp3', '.m4a', '.wav'}
ALLOWED_ATTACHMENT_CONTENT_TYPES = {
    'application/pdf',
    'image/png',
    'image/jpeg',
    'image/gif',
    'image/webp',
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'audio/webm',
    'audio/ogg',
    'audio/mpeg',
    'audio/mp4',
    'audio/x-m4a',
    'audio/wav',
    'audio/x-wav',
}
IMAGE_ATTACHMENT_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}
DOCUMENT_ATTACHMENT_EXTENSIONS = {'.doc', '.docx'}
AUDIO_ATTACHMENT_EXTENSIONS = {'.webm', '.ogg', '.mp3', '.m4a', '.wav'}
TYPING_TIMEOUT_SECONDS = 8


def _current_session_id(request):
    current = request.session.get('current_session', {})
    return current.get('Id') or current.get('id') or current.get('SessionID')


def _current_school_id(request):
    current = request.session.get('current_session', {})
    return current.get('SchoolID')


def _user_role(request):
    groups = set(request.user.groups.values_list('name', flat=True))
    if 'Admin' in groups or 'Owner' in groups:
        return 'admin'
    if 'Teaching' in groups:
        return 'teacher'
    if 'Student' in groups:
        return 'student'
    return 'user'


def _base_template_for_role(role):
    if role == 'teacher':
        return 'teacherApp/index.html'
    if role == 'student':
        return 'studentApp/index.html'
    return 'managementApp/index.html'


def _communication_permissions(user):
    actions = ('view', 'add', 'edit', 'delete', 'approve', 'report')
    if is_owner_or_admin(user) or not user_has_management_access(user):
        return {action: True for action in actions}
    return {
        action: has_management_permission(user, 'communication', action)
        for action in actions
    }


def _room_route_name_for_request(request):
    namespace = request.resolver_match.namespace if request.resolver_match else ''
    if namespace == 'teacherApp':
        return 'teacherApp:teacher_chat_room'
    if namespace == 'studentApp':
        return 'studentApp:student_chat_room'
    return 'chatApp:room'


def _role_for_user(user):
    if user.groups.filter(name__in=['Admin', 'Owner']).exists():
        return ChatParticipant.ROLE_ADMIN
    if user.groups.filter(name='Teaching').exists():
        return ChatParticipant.ROLE_TEACHER
    if user.groups.filter(name='Student').exists():
        return ChatParticipant.ROLE_STUDENT
    return ChatParticipant.ROLE_ADMIN


def _validate_attachment(attachment):
    if not attachment:
        return
    if attachment.size > MAX_ATTACHMENT_SIZE:
        raise ValidationError('Attachment must be 5 MB or smaller.')
    filename = (attachment.name or '').lower()
    extension = ''
    if '.' in filename:
        extension = filename[filename.rfind('.'):]
    content_type = (getattr(attachment, 'content_type', '') or '').split(';', 1)[0].strip()
    if extension not in ALLOWED_ATTACHMENT_EXTENSIONS or content_type not in ALLOWED_ATTACHMENT_CONTENT_TYPES:
        raise ValidationError('Only PDF, image, audio, DOC, and DOCX attachments are allowed.')


def _is_room_manager(request, room):
    role = _user_role(request)
    if role == 'admin':
        return True
    participant = room.participants.filter(userID=request.user).first()
    return bool(
        role == 'teacher'
        and participant
        and participant.role in {ChatParticipant.ROLE_ADMIN, ChatParticipant.ROLE_TEACHER}
        and room.roomType != ChatRoom.ROOM_TYPE_DIRECT
    )


def _is_participant_muted(participant):
    if not participant or not participant.isMuted:
        return False
    if participant.mutedUntil and participant.mutedUntil <= timezone.now():
        participant.isMuted = False
        participant.canPost = True
        participant.mutedUntil = None
        participant.save(update_fields=['isMuted', 'canPost', 'mutedUntil'])
        return False
    return True


def _mute_participant(participant, minutes):
    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        minutes = 60
    if minutes <= 0:
        minutes = 60
    participant.isMuted = True
    participant.canPost = False
    participant.mutedUntil = timezone.now() + timezone.timedelta(minutes=minutes)
    participant.save(update_fields=['isMuted', 'canPost', 'mutedUntil'])


def _unmute_participant(participant):
    participant.isMuted = False
    participant.canPost = True
    participant.mutedUntil = None
    participant.save(update_fields=['isMuted', 'canPost', 'mutedUntil'])


def _is_notification_muted(participant):
    if not participant:
        return False
    if participant.notificationMuted and participant.notificationMutedUntil and participant.notificationMutedUntil <= timezone.now():
        participant.notificationMuted = False
        participant.notificationMutedUntil = None
        if participant.notificationLevel == ChatParticipant.NOTIFY_OFF:
            participant.notificationLevel = ChatParticipant.NOTIFY_ALL
        participant.save(update_fields=['notificationMuted', 'notificationMutedUntil', 'notificationLevel'])
        return False
    return bool(participant.notificationMuted or participant.notificationLevel == ChatParticipant.NOTIFY_OFF)


def _notification_status_payload(participant):
    is_muted = _is_notification_muted(participant)
    level = participant.notificationLevel if participant else ChatParticipant.NOTIFY_ALL
    muted_until = participant.notificationMutedUntil if participant else None
    if is_muted and muted_until:
        label = f'Muted until {muted_until.strftime("%d %b, %I:%M %p")}'
    elif is_muted:
        label = 'Notifications off'
    elif level == ChatParticipant.NOTIFY_MENTIONS:
        label = 'Mentions only'
    else:
        label = 'All messages'
    return {
        'notification_level': level,
        'notification_muted': is_muted,
        'notification_muted_until': muted_until.strftime('%d %b %Y, %I:%M %p') if is_muted and muted_until else '',
        'label': label,
    }


def _notification_muted_until(value):
    if value == 'today':
        now = timezone.localtime(timezone.now())
        return now.replace(hour=23, minute=59, second=59, microsecond=0)
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        minutes = 0
    if minutes > 0:
        return timezone.now() + timezone.timedelta(minutes=minutes)
    return None


def _participant_users_for_standard(standard_id, session_id):
    users = []
    standards = Standard.objects.select_related('classTeacher__userID').filter(pk=standard_id, isDeleted=False)
    for standard in standards:
        if standard.classTeacher and standard.classTeacher.userID_id:
            users.append(standard.classTeacher.userID)
    teachers = TeacherDetail.objects.filter(
        assignsubjectstoteacher__assignedSubjectID__standardID_id=standard_id,
        assignsubjectstoteacher__sessionID_id=session_id,
        assignsubjectstoteacher__isDeleted=False,
        isDeleted=False,
        userID__isnull=False,
    ).select_related('userID').distinct()
    users.extend([teacher.userID for teacher in teachers])
    students = Student.objects.filter(
        standardID_id=standard_id,
        sessionID_id=session_id,
        isDeleted=False,
        userID__isnull=False,
    ).select_related('userID')
    users.extend([student.userID for student in students])
    return users


def _add_participants(room, users, read_only_students=False):
    seen = set()
    for user in users:
        if not user or user.id in seen:
            continue
        seen.add(user.id)
        role = _role_for_user(user)
        ChatParticipant.objects.get_or_create(
            roomID=room,
            userID=user,
            defaults={
                'role': role,
                'canPost': not (read_only_students and role == ChatParticipant.ROLE_STUDENT),
            },
        )


def _conversation_target_queryset(request, role, session_id, school_id):
    users = User.objects.none()
    if role == 'admin':
        teachers = User.objects.filter(
            teacherdetail__schoolID_id=school_id,
            teacherdetail__sessionID_id=session_id,
            teacherdetail__isDeleted=False,
        )
        students = User.objects.filter(
            student__schoolID_id=school_id,
            student__sessionID_id=session_id,
            student__isDeleted=False,
        )
        users = (teachers | students).exclude(pk=request.user.pk).distinct()
    elif role == 'teacher':
        teacher = TeacherDetail.objects.filter(userID_id=request.user.id, isDeleted=False).order_by('-datetime').first()
        if not teacher:
            return users
        assigned_class_ids = AssignSubjectsToTeacher.objects.filter(
            teacherID=teacher,
            sessionID_id=session_id,
            isDeleted=False,
            assignedSubjectID__isDeleted=False,
        ).values_list('assignedSubjectID__standardID_id', flat=True)
        class_teacher_ids = Standard.objects.filter(
            classTeacher=teacher,
            sessionID_id=session_id,
            isDeleted=False,
        ).values_list('id', flat=True)
        teacher_class_ids = list(set(list(assigned_class_ids) + list(class_teacher_ids)))
        users = User.objects.filter(
            Q(
                student__schoolID_id=school_id,
                student__standardID_id__in=teacher_class_ids,
                student__sessionID_id=session_id,
                student__isDeleted=False,
            )
            | Q(
                teacherdetail__schoolID_id=school_id,
                teacherdetail__sessionID_id=session_id,
                teacherdetail__isDeleted=False,
                teacherdetail__userID__isnull=False,
            )
            | Q(groups__name__in=['Admin', 'Owner'])
        ).exclude(pk=request.user.pk).distinct()
    elif role == 'student':
        student = Student.objects.filter(userID_id=request.user.id, isDeleted=False).order_by('-datetime').first()
        teacher_users = User.objects.none()
        if student and student.standardID_id:
            teacher_users = User.objects.filter(
                Q(teacherdetail__id=student.standardID.classTeacher_id)
                | Q(teacherdetail__assignsubjectstoteacher__assignedSubjectID__standardID_id=student.standardID_id),
                teacherdetail__schoolID_id=school_id,
                teacherdetail__sessionID_id=session_id,
                teacherdetail__isDeleted=False,
                teacherdetail__userID__isnull=False,
            ).distinct()
        users = teacher_users.exclude(pk=request.user.pk)
    return users


def _conversation_targets(request, role, session_id, school_id):
    users = _conversation_target_queryset(request, role, session_id, school_id)
    return [
        _conversation_target_payload(user, session_id)
        for user in users.order_by('first_name', 'username')
    ]


def _conversation_target_payload(user, session_id):
    name = display_name(user)
    role = _role_for_user(user)
    badge = role.title()
    details = []
    student = Student.objects.select_related('standardID').filter(
        userID_id=user.id,
        sessionID_id=session_id,
        isDeleted=False,
    ).order_by('-datetime').first()
    if student:
        badge = 'Student'
        if student.standardID:
            class_label = student.standardID.name or 'Class'
            if student.standardID.section:
                class_label = f'{class_label} - {student.standardID.section}'
            details.append(f'Class {class_label}')
        if student.roll:
            details.append(f'Roll {student.roll}')
        if student.registrationCode:
            details.append(f'Reg {student.registrationCode}')
    else:
        teacher = TeacherDetail.objects.filter(
            userID_id=user.id,
            sessionID_id=session_id,
            isDeleted=False,
        ).order_by('-datetime').first()
        if teacher:
            badge = 'Teacher'
            if teacher.currentPosition:
                details.append(teacher.currentPosition)
            if teacher.employeeCode:
                details.append(f'Emp {teacher.employeeCode}')
            subjects = AssignSubjectsToTeacher.objects.select_related(
                'assignedSubjectID__subjectID',
                'assignedSubjectID__standardID',
            ).filter(
                teacherID=teacher,
                sessionID_id=session_id,
                isDeleted=False,
                assignedSubjectID__isDeleted=False,
            )[:3]
            subject_labels = []
            for assigned in subjects:
                assigned_subject = assigned.assignedSubjectID
                if not assigned_subject:
                    continue
                subject_name = assigned_subject.subjectID.name if assigned_subject.subjectID else 'Subject'
                class_name = assigned_subject.standardID.name if assigned_subject.standardID else ''
                section = f' - {assigned_subject.standardID.section}' if assigned_subject.standardID and assigned_subject.standardID.section else ''
                subject_labels.append(f'{subject_name}{f" ({class_name}{section})" if class_name else ""}')
            if subject_labels:
                details.append(', '.join(subject_labels))
    detail = ' | '.join(details) if details else 'School management user'
    list_label = f'{name} - {badge} | {detail}'
    return {
        'id': user.id,
        'label': name,
        'detail': detail,
        'badge': badge,
        'list_label': list_label,
        'option_label': f'{name} - {badge}',
        'role': role,
    }


def _available_standards(request, role, session_id, school_id):
    qs = Standard.objects.filter(sessionID_id=session_id, schoolID_id=school_id, isDeleted=False).order_by('name', 'section')
    if role == 'teacher':
        teacher = TeacherDetail.objects.filter(userID_id=request.user.id, isDeleted=False).order_by('-datetime').first()
        assigned_ids = AssignSubjectsToTeacher.objects.filter(
            teacherID=teacher,
            sessionID_id=session_id,
            isDeleted=False,
            assignedSubjectID__isDeleted=False,
        ).values_list('assignedSubjectID__standardID_id', flat=True)
        return qs.filter(Q(id__in=assigned_ids) | Q(classTeacher=teacher)).distinct()
    if role == 'student':
        student = Student.objects.filter(userID_id=request.user.id, isDeleted=False).order_by('-datetime').first()
        return qs.filter(pk=student.standardID_id) if student else qs.none()
    return qs


def _available_subject_assignments(request, role, session_id, school_id):
    qs = AssignSubjectsToClass.objects.select_related('standardID', 'subjectID').filter(
        sessionID_id=session_id,
        schoolID_id=school_id,
        isDeleted=False,
        standardID__isDeleted=False,
        subjectID__isDeleted=False,
    ).order_by('standardID__name', 'standardID__section', 'subjectID__name')
    if role == 'teacher':
        teacher = TeacherDetail.objects.filter(userID_id=request.user.id, isDeleted=False).order_by('-datetime').first()
        assigned_ids = AssignSubjectsToTeacher.objects.filter(
            teacherID=teacher,
            sessionID_id=session_id,
            isDeleted=False,
        ).values_list('assignedSubjectID_id', flat=True)
        return qs.filter(id__in=assigned_ids)
    if role == 'student':
        student = Student.objects.filter(userID_id=request.user.id, isDeleted=False).order_by('-datetime').first()
        return qs.filter(standardID_id=student.standardID_id) if student else qs.none()
    return qs


def _rooms_for_user(request):
    return rooms_for_user(request.user)


def _room_cards(request):
    return room_cards_for_user(request.user)


def _mark_room_read(room, user):
    last_message = room.messages.filter(isDeleted=False).order_by('-datetime').first()
    participant = room.participants.filter(userID=user).first()
    if participant:
        participant.lastReadMessage = last_message
        participant.lastReadAt = timezone.now()
        participant.save(update_fields=['lastReadMessage', 'lastReadAt'])
    if last_message:
        receipts = [
            MessageReadReceipt(messageID=message, userID=user)
            for message in room.messages.filter(isDeleted=False).exclude(senderID=user)
        ]
        MessageReadReceipt.objects.bulk_create(receipts, ignore_conflicts=True)


def _message_payload(message, user):
    local_dt = _local_message_datetime(message.datetime)
    attachment = _attachment_payload(message)
    communication_permissions = _communication_permissions(user)
    is_own = message.senderID_id == user.id
    return {
        'id': message.id,
        'body': message.body or '',
        'sender_name': display_name(message.senderID),
        'sender_id': message.senderID_id,
        'is_own': is_own,
        'created_at': local_dt.strftime('%d %b %Y, %I:%M %p') if local_dt else '',
        'time_label': local_dt.strftime('%I:%M %p') if local_dt else '',
        'date_key': local_dt.date().isoformat() if local_dt else '',
        'date_label': _message_date_label(local_dt) if local_dt else '',
        'attachment': attachment,
        'attachment_url': attachment['url'] if attachment else '',
        'attachment_name': attachment['name'] if attachment else '',
        'has_attachment': bool(attachment),
        'is_edited': message.isEdited,
        'is_deleted': message.isDeleted,
        'can_edit': is_own and not message.isDeleted and communication_permissions['edit'],
        'can_delete': is_own and not message.isDeleted and communication_permissions['delete'],
        'can_report': (not is_own) and not message.isDeleted and communication_permissions['report'],
        'can_add_action': communication_permissions['add'],
        'read_summary': _read_summary_for_message(message, user),
        'reactions': _reaction_summary_for_message(message, user),
        'is_pinned': message.pins.exists(),
        'is_saved': message.savedBy.filter(userID=user).exists(),
        'reply_to': _reply_payload(message.replyTo) if message.replyTo_id and not message.replyTo.isDeleted else None,
    }


def _message_date_label(local_dt):
    if not local_dt:
        return ''
    message_date = local_dt.date()
    today = _local_message_datetime(timezone.now()).date()
    if message_date == today:
        return 'Today'
    if message_date == today - timezone.timedelta(days=1):
        return 'Yesterday'
    return local_dt.strftime('%d %b %Y')


def _local_message_datetime(value):
    if not value:
        return None
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone.get_current_timezone())
    return timezone.localtime(value)


def _format_attachment_size(size):
    if not size:
        return ''
    if size < 1024:
        return f'{size} B'
    if size < 1024 * 1024:
        return f'{size / 1024:.1f} KB'
    return f'{size / (1024 * 1024):.1f} MB'


def _attachment_payload(message):
    if not message.attachment:
        return None
    try:
        url = message.attachment.url
    except ValueError:
        url = ''
    raw_name = message.attachment.name.rsplit('/', 1)[-1]
    name = raw_name or 'Attachment'
    extension = os.path.splitext(name)[1].lower()
    if extension in IMAGE_ATTACHMENT_EXTENSIONS:
        kind = 'image'
        icon = 'image outline'
        type_label = extension.lstrip('.').upper() + ' image'
    elif extension in AUDIO_ATTACHMENT_EXTENSIONS:
        kind = 'audio'
        icon = 'microphone'
        type_label = 'Voice note' if message.messageType == ChatMessage.MESSAGE_VOICE else 'Audio file'
    elif extension == '.pdf':
        kind = 'pdf'
        icon = 'file pdf outline'
        type_label = 'PDF document'
    elif extension in DOCUMENT_ATTACHMENT_EXTENSIONS:
        kind = 'document'
        icon = 'file word outline'
        type_label = 'Word document'
    else:
        kind = 'file'
        icon = 'file outline'
        type_label = 'File attachment'
    try:
        size = message.attachment.size
    except (OSError, ValueError):
        size = 0
    return {
        'url': url,
        'name': name,
        'extension': extension.lstrip('.').upper(),
        'kind': kind,
        'icon': icon,
        'type_label': type_label,
        'size_label': _format_attachment_size(size),
    }


def _reply_payload(message):
    return {
        'id': message.id,
        'sender_name': display_name(message.senderID),
        'excerpt': _message_excerpt(message),
    }


def _pinned_payload(pin):
    message = pin.messageID
    return {
        'id': pin.id,
        'message_id': message.id,
        'excerpt': _message_excerpt(message),
        'sender_name': display_name(message.senderID),
        'pinned_by': display_name(pin.pinnedBy) if pin.pinnedBy_id else 'Unknown',
        'pinned_at': pin.datetime.strftime('%d %b %Y, %I:%M %p') if pin.datetime else '',
    }


def _pinned_messages_payload(room):
    return [
        _pinned_payload(pin)
        for pin in room.pinnedMessages.filter(messageID__isDeleted=False)
        .select_related('messageID', 'messageID__senderID', 'pinnedBy')
        .order_by('-datetime')[:MAX_PINNED_MESSAGES]
    ]


def _saved_payload(saved):
    message = saved.messageID
    return {
        'id': saved.id,
        'message_id': message.id,
        'excerpt': _message_excerpt(message),
        'sender_name': display_name(message.senderID),
        'saved_at': saved.datetime.strftime('%d %b %Y, %I:%M %p') if saved.datetime else '',
    }


def _saved_messages_payload(room, user):
    return [
        _saved_payload(saved)
        for saved in ChatSavedMessage.objects.filter(
            userID=user,
            messageID__roomID=room,
            messageID__isDeleted=False,
        )
        .select_related('messageID', 'messageID__senderID')
        .order_by('-datetime')[:30]
    ]


def _reaction_summary_for_message(message, user):
    grouped = {
        option['key']: {
            'key': option['key'],
            'label': option['label'],
            'icon': option['icon'],
            'count': 0,
            'active': False,
            'users': [],
        }
        for option in REACTION_OPTIONS
    }
    for reaction in message.reactions.select_related('userID').order_by('datetime'):
        item = grouped.get(reaction.reactionType)
        if not item:
            continue
        item['count'] += 1
        item['users'].append(display_name(reaction.userID))
        if reaction.userID_id == user.id:
            item['active'] = True
    return list(grouped.values())


def _message_excerpt(message):
    body = (message.body or '').strip()
    if body:
        return body[:140] + ('...' if len(body) > 140 else '')
    if message.attachment:
        return message.attachment.name.rsplit('/', 1)[-1]
    return 'Attachment'


def _reply_target_for_room(room, reply_to_id):
    if not reply_to_id:
        return None
    try:
        return room.messages.filter(pk=int(reply_to_id), isDeleted=False).first()
    except (TypeError, ValueError):
        return None


def _read_summary_for_message(message, user):
    if not message or message.senderID_id != user.id:
        return ''
    receipts = list(
        message.readReceipts.exclude(userID_id=user.id)
        .select_related('userID')
        .order_by('readAt')[:6]
    )
    if not receipts:
        return 'Sent'
    names = [display_name(receipt.userID) for receipt in receipts]
    total = message.readReceipts.exclude(userID_id=user.id).count()
    if total <= 2:
        return 'Seen by ' + ', '.join(names)
    return f'Seen by {total}'


def _participant_payload(participant):
    is_muted = _is_participant_muted(participant)
    return {
        'id': participant.id,
        'user_id': participant.userID_id,
        'name': display_name(participant.userID),
        'role': participant.get_role_display(),
        'can_post': participant.canPost,
        'is_muted': is_muted,
        'muted_until': participant.mutedUntil.strftime('%d %b %Y, %I:%M %p') if is_muted and participant.mutedUntil else '',
        'notification_muted': participant.notificationMuted,
    }


def _create_chat_message(room, sender, body='', attachment=None, reply_to=None, message_type=None):
    message = ChatMessage.objects.create(
        roomID=room,
        senderID=sender,
        body=body,
        attachment=attachment,
        replyTo=reply_to,
        messageType=message_type or (ChatMessage.MESSAGE_ATTACHMENT if attachment else ChatMessage.MESSAGE_TEXT),
    )
    room.save(update_fields=['lastUpdatedOn'])
    send_chat_push_notifications(message)
    return message


def _room_summary_payload(user):
    cards = room_cards_for_user(user)
    return {
        'total_unread': sum(card['unread_count'] for card in cards),
        'rooms': [
            {
                'id': card['room'].id,
                'unread_count': card['unread_count'],
                'preview': (
                    f"{card['last_sender']}: "
                    f"{(card['last_message'].body or 'Attachment')[:48]}"
                ) if card['last_message'] else 'No messages yet',
            }
            for card in cards
        ],
    }


def _typing_cache_key(room_id):
    return f'chat-room-typing-{room_id}'


def _set_user_typing(room, user):
    typing = cache.get(_typing_cache_key(room.id), {})
    now = timezone.now()
    typing[str(user.id)] = {
        'name': display_name(user),
        'until': (now + timezone.timedelta(seconds=TYPING_TIMEOUT_SECONDS)).timestamp(),
    }
    cache.set(_typing_cache_key(room.id), typing, timeout=TYPING_TIMEOUT_SECONDS + 3)


def _typing_users_payload(room, user):
    typing = cache.get(_typing_cache_key(room.id), {})
    now_ts = timezone.now().timestamp()
    active = {}
    users = []
    for user_id, item in typing.items():
        if item.get('until', 0) <= now_ts:
            continue
        active[user_id] = item
        if str(user.id) != str(user_id):
            users.append(item.get('name') or 'Someone')
    if active != typing:
        cache.set(_typing_cache_key(room.id), active, timeout=TYPING_TIMEOUT_SECONDS + 3)
    return users[:4]


def _csv_response(filename):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


def _local_datetime(value):
    if not value:
        return ''
    if timezone.is_aware(value):
        value = timezone.localtime(value)
    return value.strftime('%Y-%m-%d %H:%M:%S')


def _clean_filename(value):
    safe = ''.join(char if char.isalnum() or char in {'-', '_'} else '-' for char in (value or 'export'))
    return '-'.join(part for part in safe.split('-') if part)[:80] or 'export'


def _create_direct_room(request, role, target_user_id, session_id, school_id):
    target = get_object_or_404(
        _conversation_target_queryset(request, role, session_id, school_id),
        pk=target_user_id,
    )
    existing = ChatRoom.objects.filter(
        roomType=ChatRoom.ROOM_TYPE_DIRECT,
        sessionID_id=session_id,
        participants__userID=request.user,
    ).filter(participants__userID=target).distinct().first()
    if existing:
        return existing
    room = ChatRoom.objects.create(
        title=f'{display_name(request.user)} and {display_name(target)}',
        roomType=ChatRoom.ROOM_TYPE_DIRECT,
        schoolID_id=school_id,
        sessionID_id=session_id,
        createdBy=request.user,
    )
    _add_participants(room, [request.user, target])
    return room


def _create_group_room(request, role, session_id, school_id):
    room_type = request.POST.get('room_type')
    title = (request.POST.get('title') or '').strip()
    if room_type == ChatRoom.ROOM_TYPE_CLASS:
        standard = get_object_or_404(_available_standards(request, role, session_id, school_id), pk=request.POST.get('standard_id'))
        existing = ChatRoom.objects.filter(
            roomType=ChatRoom.ROOM_TYPE_CLASS,
            schoolID_id=school_id,
            sessionID_id=session_id,
            standardID=standard,
            isActive=True,
        ).first()
        if existing:
            _add_participants(existing, [request.user])
            return existing
        title = title or f'{standard.name} {standard.section or ""}'.strip()
        room = ChatRoom.objects.create(
            title=title,
            roomType=ChatRoom.ROOM_TYPE_CLASS,
            schoolID_id=school_id,
            sessionID_id=session_id,
            standardID=standard,
            createdBy=request.user,
        )
        _add_participants(room, [request.user] + _participant_users_for_standard(standard.id, session_id))
        return room
    if room_type == ChatRoom.ROOM_TYPE_SUBJECT:
        assignment = get_object_or_404(_available_subject_assignments(request, role, session_id, school_id), pk=request.POST.get('subject_assignment_id'))
        existing = ChatRoom.objects.filter(
            roomType=ChatRoom.ROOM_TYPE_SUBJECT,
            schoolID_id=school_id,
            sessionID_id=session_id,
            standardID=assignment.standardID,
            subjectID=assignment.subjectID,
            isActive=True,
        ).first()
        if existing:
            _add_participants(existing, [request.user])
            return existing
        subject_name = assignment.subjectID.name if assignment.subjectID else 'Subject'
        class_name = assignment.standardID.name if assignment.standardID else 'Class'
        section = f' - {assignment.standardID.section}' if assignment.standardID and assignment.standardID.section else ''
        title = title or f'{class_name}{section} {subject_name}'
        room = ChatRoom.objects.create(
            title=title,
            roomType=ChatRoom.ROOM_TYPE_SUBJECT,
            schoolID_id=school_id,
            sessionID_id=session_id,
            standardID=assignment.standardID,
            subjectID=assignment.subjectID,
            createdBy=request.user,
        )
        users = _participant_users_for_standard(assignment.standardID_id, session_id)
        _add_participants(room, [request.user] + users)
        return room
    if room_type == ChatRoom.ROOM_TYPE_ANNOUNCEMENT and role == 'admin':
        target = request.POST.get('announcement_target')
        title = title or 'School Announcement'
        room = ChatRoom.objects.create(
            title=title,
            roomType=ChatRoom.ROOM_TYPE_ANNOUNCEMENT,
            schoolID_id=school_id,
            sessionID_id=session_id,
            createdBy=request.user,
            isReadOnly=True,
        )
        users = [request.user]
        if target == 'teachers':
            users.extend([t.userID for t in TeacherDetail.objects.filter(schoolID_id=school_id, sessionID_id=session_id, isDeleted=False, userID__isnull=False)])
        elif target == 'students':
            users.extend([s.userID for s in Student.objects.filter(schoolID_id=school_id, sessionID_id=session_id, isDeleted=False, userID__isnull=False)])
        else:
            for standard in Standard.objects.filter(schoolID_id=school_id, sessionID_id=session_id, isDeleted=False):
                users.extend(_participant_users_for_standard(standard.id, session_id))
        _add_participants(room, users, read_only_students=True)
        return room
    return None


@login_required
@check_groups('Admin', 'Owner', 'Teaching', 'Student')
def inbox(request, room_id=None):
    role = _user_role(request)
    session_id = _current_session_id(request)
    school_id = _current_school_id(request)
    if not session_id or not school_id:
        messages.error(request, 'Please select an active school session before opening chat.')
        return redirect('/')

    active_room = None
    participant = None
    if room_id:
        active_room = get_object_or_404(_rooms_for_user(request), pk=room_id)
        participant = active_room.participants.filter(userID=request.user).first()

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'create_direct':
            room = _create_direct_room(request, role, request.POST.get('target_user_id'), session_id, school_id)
            return redirect(_room_route_name_for_request(request), room_id=room.id)
        if action == 'create_group' and role in {'admin', 'teacher'}:
            room = _create_group_room(request, role, session_id, school_id)
            if room:
                messages.success(request, 'Chat room created successfully.')
                return redirect(_room_route_name_for_request(request), room_id=room.id)
            messages.error(request, 'Unable to create that chat room.')
        if action == 'send_message' and active_room and participant and participant.canPost and not _is_participant_muted(participant):
            body = (request.POST.get('body') or '').strip()
            attachment = request.FILES.get('attachment')
            if body or attachment:
                try:
                    _validate_attachment(attachment)
                except ValidationError as exc:
                    messages.error(request, exc.messages[0])
                    return redirect(_room_route_name_for_request(request), room_id=active_room.id)
                _create_chat_message(
                    room=active_room,
                    sender=request.user,
                    body=body,
                    attachment=attachment,
                    reply_to=_reply_target_for_room(active_room, request.POST.get('reply_to_id')),
                    message_type=ChatMessage.MESSAGE_VOICE if request.POST.get('message_type') == ChatMessage.MESSAGE_VOICE else None,
                )
                return redirect(_room_route_name_for_request(request), room_id=active_room.id)
            messages.error(request, 'Write a message or attach a file before sending.')
        if action == 'edit_message' and active_room:
            message_obj = get_object_or_404(active_room.messages, pk=request.POST.get('message_id'), senderID=request.user, isDeleted=False)
            body = (request.POST.get('body') or '').strip()
            if body:
                message_obj.body = body
                message_obj.isEdited = True
                message_obj.save(update_fields=['body', 'isEdited', 'lastUpdatedOn'])
                active_room.save(update_fields=['lastUpdatedOn'])
            return redirect(_room_route_name_for_request(request), room_id=active_room.id)
        if action == 'delete_message' and active_room:
            message_obj = get_object_or_404(active_room.messages, pk=request.POST.get('message_id'), senderID=request.user, isDeleted=False)
            message_obj.isDeleted = True
            message_obj.body = ''
            message_obj.save(update_fields=['isDeleted', 'body', 'lastUpdatedOn'])
            active_room.save(update_fields=['lastUpdatedOn'])
            return redirect(_room_route_name_for_request(request), room_id=active_room.id)
        if action == 'participant_update' and active_room and _is_room_manager(request, active_room):
            target_participant = get_object_or_404(active_room.participants, pk=request.POST.get('participant_id'))
            participant_action = request.POST.get('participant_action')
            if target_participant.userID_id == request.user.id:
                messages.error(request, 'You cannot change your own room access.')
                return redirect(_room_route_name_for_request(request), room_id=active_room.id)
            if participant_action == 'mute':
                _mute_participant(target_participant, request.POST.get('mute_minutes'))
            elif participant_action == 'unmute':
                _unmute_participant(target_participant)
            elif participant_action == 'remove':
                target_participant.delete()
            return redirect(_room_route_name_for_request(request), room_id=active_room.id)
        if action == 'participant_add' and active_room and _is_room_manager(request, active_room):
            target = get_object_or_404(
                _conversation_target_queryset(request, role, session_id, school_id),
                pk=request.POST.get('target_user_id'),
            )
            ChatParticipant.objects.get_or_create(
                roomID=active_room,
                userID=target,
                defaults={'role': _role_for_user(target), 'canPost': True},
            )
            return redirect(_room_route_name_for_request(request), room_id=active_room.id)
        if action == 'toggle_notifications' and active_room and participant:
            participant.notificationMuted = not _is_notification_muted(participant)
            participant.notificationMutedUntil = None
            participant.notificationLevel = ChatParticipant.NOTIFY_OFF if participant.notificationMuted else ChatParticipant.NOTIFY_ALL
            participant.save(update_fields=['notificationMuted', 'notificationMutedUntil', 'notificationLevel'])
            return redirect(_room_route_name_for_request(request), room_id=active_room.id)
        if action == 'report_message' and active_room:
            message_id = request.POST.get('message_id')
            reason = (request.POST.get('reason') or '').strip()
            message_obj = get_object_or_404(active_room.messages, pk=message_id, isDeleted=False)
            MessageReport.objects.create(messageID=message_obj, reportedBy=request.user, reason=reason)
            messages.success(request, 'Message reported for review.')
            return redirect(_room_route_name_for_request(request), room_id=active_room.id)

    room_messages = []
    unread_after_message_id = None
    if active_room:
        unread_after_message_id = participant.lastReadMessage_id if participant else None
        _mark_room_read(active_room, request.user)
        room_messages = active_room.messages.filter(isDeleted=False).select_related('senderID').order_by('datetime')
        unread_divider_added = False
        rows = []
        previous_message = None
        previous_date_key = ''
        for message in room_messages:
            local_dt = _local_message_datetime(message.datetime)
            date_key = local_dt.date().isoformat() if local_dt else ''
            is_grouped = (
                bool(previous_message)
                and previous_message.senderID_id == message.senderID_id
                and previous_date_key == date_key
                and message.datetime
                and previous_message.datetime
                and (_local_message_datetime(message.datetime) - _local_message_datetime(previous_message.datetime)).total_seconds() <= 300
            )
            show_unread_divider = (
                bool(unread_after_message_id)
                and not unread_divider_added
                and message.senderID_id != request.user.id
                and message.id > unread_after_message_id
            )
            if show_unread_divider:
                unread_divider_added = True
            rows.append({
                'message': message,
                'sender_name': display_name(message.senderID),
                'is_own': message.senderID_id == request.user.id,
                'read_summary': _read_summary_for_message(message, request.user),
                'reactions': _reaction_summary_for_message(message, request.user),
                'is_pinned': message.pins.exists(),
                'is_saved': message.savedBy.filter(userID=request.user).exists(),
                'reply_to': _reply_payload(message.replyTo) if message.replyTo_id and not message.replyTo.isDeleted else None,
                'attachment': _attachment_payload(message),
                'show_unread_divider': show_unread_divider,
                'show_date_separator': date_key != previous_date_key,
                'date_label': _message_date_label(local_dt) if local_dt else '',
                'time_label': local_dt.strftime('%I:%M %p') if local_dt else '',
                'is_grouped': is_grouped,
            })
            previous_message = message
            previous_date_key = date_key
        room_messages = rows

    room_participants = []
    if active_room:
        room_participants = [
            _participant_payload(p)
            for p in active_room.participants.select_related('userID').order_by('role', 'userID__first_name', 'userID__username')
        ]

    context = {
        'communication_permissions': _communication_permissions(request.user),
        'base_template': _base_template_for_role(role),
        'role': role,
        'room_cards': _room_cards(request),
        'active_room': active_room,
        'active_participant': participant,
        'room_messages': room_messages,
        'unread_after_message_id': unread_after_message_id,
        'conversation_targets': _conversation_targets(request, role, session_id, school_id),
        'standards': _available_standards(request, role, session_id, school_id),
        'subject_assignments': _available_subject_assignments(request, role, session_id, school_id),
        'can_manage_room': bool(active_room and _is_room_manager(request, active_room) and _communication_permissions(request.user)['approve']),
        'room_participants': room_participants,
        'max_attachment_mb': MAX_ATTACHMENT_SIZE // (1024 * 1024),
        'notification_status': _notification_status_payload(participant) if participant else {},
        'notification_levels': ChatParticipant.NOTIFICATION_LEVEL_CHOICES,
        'notification_mute_options': NOTIFICATION_MUTE_OPTIONS,
        'pinned_messages': _pinned_messages_payload(active_room) if active_room else [],
        'saved_messages': _saved_messages_payload(active_room, request.user) if active_room else [],
        'max_pinned_messages': MAX_PINNED_MESSAGES,
        'mute_duration_options': MUTE_DURATION_OPTIONS,
        'typing_timeout_seconds': TYPING_TIMEOUT_SECONDS,
    }
    return render(request, 'chatApp/inbox.html', context)


@login_required
@check_groups('Admin', 'Owner', 'Teaching', 'Student')
@require_GET
def room_messages_api(request, room_id):
    room = get_object_or_404(rooms_for_user(request.user), pk=room_id)
    after_id = request.GET.get('after_id')
    messages_qs = room.messages.filter(isDeleted=False).select_related('senderID').order_by('datetime')
    if after_id:
        try:
            messages_qs = messages_qs.filter(id__gt=int(after_id))
        except (TypeError, ValueError):
            pass
    payload = [_message_payload(message, request.user) for message in messages_qs]
    _mark_room_read(room, request.user)
    return JsonResponse({
        'messages': payload,
        'summary': _room_summary_payload(request.user),
        'pinned_messages': _pinned_messages_payload(room),
        'saved_messages': _saved_messages_payload(room, request.user),
        'typing_users': _typing_users_payload(room, request.user),
    })


@login_required
@check_groups('Admin', 'Owner', 'Teaching', 'Student')
@require_POST
def room_typing_api(request, room_id):
    room = get_object_or_404(rooms_for_user(request.user), pk=room_id)
    if not room.participants.filter(userID=request.user).exists():
        return JsonResponse({'ok': False, 'error': 'You are not a participant in this room.'}, status=403)
    _set_user_typing(room, request.user)
    return JsonResponse({'ok': True})


@login_required
@check_groups('Admin', 'Owner', 'Teaching', 'Student')
@require_GET
def room_search_api(request, room_id):
    room = get_object_or_404(rooms_for_user(request.user), pk=room_id)
    query = (request.GET.get('q') or '').strip()
    attachments_only = request.GET.get('attachments_only') == '1'

    messages_qs = room.messages.filter(isDeleted=False).select_related('senderID').order_by('-datetime')
    if query:
        messages_qs = messages_qs.filter(
            Q(body__icontains=query)
            | Q(senderID__username__icontains=query)
            | Q(senderID__first_name__icontains=query)
            | Q(senderID__last_name__icontains=query)
            | Q(attachment__icontains=query)
        )
    if attachments_only:
        messages_qs = messages_qs.filter(attachment__isnull=False).exclude(attachment='')

    results = []
    limit = 100 if attachments_only else 50
    for message in messages_qs[:limit]:
        payload = _message_payload(message, request.user)
        payload['excerpt'] = _message_excerpt(message)
        results.append(payload)

    return JsonResponse({
        'results': results,
        'count': len(results),
        'query': query,
        'attachments_only': attachments_only,
    })


@login_required
@check_groups('Admin', 'Owner', 'Teaching', 'Student')
@require_POST
def send_message_api(request, room_id):
    room = get_object_or_404(rooms_for_user(request.user), pk=room_id)
    participant = room.participants.filter(userID=request.user).first()
    if not participant or not participant.canPost or _is_participant_muted(participant):
        return JsonResponse({'ok': False, 'error': 'Posting is disabled for this room.'}, status=403)
    body = (request.POST.get('body') or '').strip()
    attachment = request.FILES.get('attachment')
    is_voice = request.POST.get('message_type') == ChatMessage.MESSAGE_VOICE
    if not body and not attachment:
        return JsonResponse({'ok': False, 'error': 'Write a message or attach a file before sending.'}, status=400)
    try:
        _validate_attachment(attachment)
    except ValidationError as exc:
        return JsonResponse({'ok': False, 'error': exc.messages[0]}, status=400)
    reply_to = _reply_target_for_room(room, request.POST.get('reply_to_id'))
    message = _create_chat_message(
        room=room,
        sender=request.user,
        body=body or ('Voice note' if is_voice else ''),
        attachment=attachment,
        reply_to=reply_to,
        message_type=ChatMessage.MESSAGE_VOICE if is_voice else None,
    )
    _mark_room_read(room, request.user)
    return JsonResponse({
        'ok': True,
        'message': _message_payload(message, request.user),
        'summary': _room_summary_payload(request.user),
    })


@login_required
@check_groups('Admin', 'Owner', 'Teaching', 'Student')
@require_POST
def edit_message_api(request, room_id, message_id):
    room = get_object_or_404(rooms_for_user(request.user), pk=room_id)
    message = get_object_or_404(room.messages, pk=message_id, senderID=request.user, isDeleted=False)
    body = (request.POST.get('body') or '').strip()
    if not body:
        return JsonResponse({'ok': False, 'error': 'Message text is required.'}, status=400)
    message.body = body
    message.isEdited = True
    message.save(update_fields=['body', 'isEdited', 'lastUpdatedOn'])
    room.save(update_fields=['lastUpdatedOn'])
    return JsonResponse({'ok': True, 'message': _message_payload(message, request.user)})


@login_required
@check_groups('Admin', 'Owner', 'Teaching', 'Student')
@require_POST
def delete_message_api(request, room_id, message_id):
    room = get_object_or_404(rooms_for_user(request.user), pk=room_id)
    message = get_object_or_404(room.messages, pk=message_id, senderID=request.user, isDeleted=False)
    message.isDeleted = True
    message.body = ''
    message.save(update_fields=['isDeleted', 'body', 'lastUpdatedOn'])
    room.save(update_fields=['lastUpdatedOn'])
    return JsonResponse({'ok': True, 'message_id': message.id, 'summary': _room_summary_payload(request.user)})


@login_required
@check_groups('Admin', 'Owner', 'Teaching', 'Student')
@require_POST
def message_reaction_api(request, room_id, message_id):
    room = get_object_or_404(rooms_for_user(request.user), pk=room_id)
    message = get_object_or_404(room.messages, pk=message_id, isDeleted=False)
    if not room.participants.filter(userID=request.user).exists():
        return JsonResponse({'ok': False, 'error': 'You are not a participant in this room.'}, status=403)

    reaction_type = request.POST.get('reaction_type')
    if reaction_type not in REACTION_KEYS:
        return JsonResponse({'ok': False, 'error': 'Unknown reaction.'}, status=400)

    reaction, created = MessageReaction.objects.get_or_create(
        messageID=message,
        userID=request.user,
        reactionType=reaction_type,
    )
    if not created:
        reaction.delete()
    return JsonResponse({
        'ok': True,
        'message_id': message.id,
        'reactions': _reaction_summary_for_message(message, request.user),
    })


@login_required
@check_groups('Admin', 'Owner', 'Teaching', 'Student')
@require_POST
def message_pin_api(request, room_id, message_id):
    room = get_object_or_404(rooms_for_user(request.user), pk=room_id)
    if not _is_room_manager(request, room):
        return JsonResponse({'ok': False, 'error': 'You cannot pin messages in this room.'}, status=403)
    message = get_object_or_404(room.messages, pk=message_id, isDeleted=False)

    pin = ChatPinnedMessage.objects.filter(roomID=room, messageID=message).first()
    if pin:
        pin.delete()
        is_pinned = False
    else:
        if room.pinnedMessages.filter(messageID__isDeleted=False).count() >= MAX_PINNED_MESSAGES:
            return JsonResponse({
                'ok': False,
                'error': f'You can pin up to {MAX_PINNED_MESSAGES} messages in a room.',
            }, status=400)
        ChatPinnedMessage.objects.create(roomID=room, messageID=message, pinnedBy=request.user)
        is_pinned = True

    return JsonResponse({
        'ok': True,
        'message_id': message.id,
        'is_pinned': is_pinned,
        'pinned_messages': _pinned_messages_payload(room),
    })


@login_required
@check_groups('Admin', 'Owner', 'Teaching', 'Student')
@require_POST
def message_save_api(request, room_id, message_id):
    room = get_object_or_404(rooms_for_user(request.user), pk=room_id)
    message = get_object_or_404(room.messages, pk=message_id, isDeleted=False)
    saved = ChatSavedMessage.objects.filter(messageID=message, userID=request.user).first()
    if saved:
        saved.delete()
        is_saved = False
    else:
        ChatSavedMessage.objects.create(messageID=message, userID=request.user)
        is_saved = True
    return JsonResponse({
        'ok': True,
        'message_id': message.id,
        'is_saved': is_saved,
        'saved_messages': _saved_messages_payload(room, request.user),
    })


@login_required
@check_groups('Admin', 'Owner', 'Teaching', 'Student')
@require_POST
def message_forward_api(request, room_id, message_id):
    source_room = get_object_or_404(rooms_for_user(request.user), pk=room_id)
    source_message = get_object_or_404(source_room.messages, pk=message_id, isDeleted=False)
    target_room = get_object_or_404(rooms_for_user(request.user), pk=request.POST.get('target_room_id'))
    participant = target_room.participants.filter(userID=request.user).first()
    if not participant or not participant.canPost or _is_participant_muted(participant):
        return JsonResponse({'ok': False, 'error': 'You cannot post in the selected room.'}, status=403)
    prefix = f'Forwarded from {source_room.title}'
    body = (source_message.body or '').strip()
    forwarded_body = f'{prefix}\n\n{body}' if body else prefix
    message = _create_chat_message(
        room=target_room,
        sender=request.user,
        body=forwarded_body,
        attachment=source_message.attachment if source_message.attachment else None,
        message_type=source_message.messageType if source_message.messageType == ChatMessage.MESSAGE_VOICE else None,
    )
    return JsonResponse({
        'ok': True,
        'message': _message_payload(message, request.user),
        'target_room_id': target_room.id,
        'target_room_title': target_room.title,
        'summary': _room_summary_payload(request.user),
    })


@login_required
@check_groups('Admin', 'Owner', 'Teaching', 'Student')
@require_POST
def participant_update_api(request, room_id):
    room = get_object_or_404(rooms_for_user(request.user), pk=room_id)
    if not _is_room_manager(request, room):
        return JsonResponse({'ok': False, 'error': 'You cannot manage this room.'}, status=403)
    target_participant = get_object_or_404(room.participants, pk=request.POST.get('participant_id'))
    if target_participant.userID_id == request.user.id:
        return JsonResponse({'ok': False, 'error': 'You cannot change your own room access.'}, status=400)
    action = request.POST.get('participant_action')
    if action == 'mute':
        _mute_participant(target_participant, request.POST.get('mute_minutes'))
        return JsonResponse({'ok': True, 'participant': _participant_payload(target_participant)})
    if action == 'unmute':
        _unmute_participant(target_participant)
        return JsonResponse({'ok': True, 'participant': _participant_payload(target_participant)})
    if action == 'remove':
        participant_id = target_participant.id
        target_participant.delete()
        return JsonResponse({'ok': True, 'removed_participant_id': participant_id})
    return JsonResponse({'ok': False, 'error': 'Unknown participant action.'}, status=400)


@login_required
@check_groups('Admin', 'Owner', 'Teaching', 'Student')
@require_POST
def participant_add_api(request, room_id):
    room = get_object_or_404(rooms_for_user(request.user), pk=room_id)
    if not _is_room_manager(request, room):
        return JsonResponse({'ok': False, 'error': 'You cannot manage this room.'}, status=403)
    role = _user_role(request)
    session_id = room.sessionID_id or _current_session_id(request)
    school_id = room.schoolID_id or _current_school_id(request)
    target = get_object_or_404(
        _conversation_target_queryset(request, role, session_id, school_id),
        pk=request.POST.get('target_user_id'),
    )
    participant, _ = ChatParticipant.objects.get_or_create(
        roomID=room,
        userID=target,
        defaults={'role': _role_for_user(target), 'canPost': True},
    )
    return JsonResponse({'ok': True, 'participant': _participant_payload(participant)})


@login_required
@check_groups('Admin', 'Owner', 'Teaching', 'Student')
@require_POST
def notification_preference_api(request, room_id):
    room = get_object_or_404(rooms_for_user(request.user), pk=room_id)
    participant = get_object_or_404(room.participants, userID=request.user)
    level = request.POST.get('notification_level') or participant.notificationLevel
    if level not in {choice[0] for choice in ChatParticipant.NOTIFICATION_LEVEL_CHOICES}:
        return JsonResponse({'ok': False, 'error': 'Unknown notification level.'}, status=400)
    mute_for = request.POST.get('mute_for')
    participant.notificationLevel = level
    participant.notificationMutedUntil = _notification_muted_until(mute_for)
    participant.notificationMuted = bool(participant.notificationMutedUntil or level == ChatParticipant.NOTIFY_OFF)
    participant.save(update_fields=['notificationLevel', 'notificationMuted', 'notificationMutedUntil'])
    payload = _notification_status_payload(participant)
    payload['ok'] = True
    return JsonResponse(payload)


@login_required
@check_groups('Admin', 'Owner')
@require_GET
def room_messages_export(request, room_id):
    room = get_object_or_404(ChatRoom.objects.filter(isActive=True), pk=room_id)
    today = timezone.now().date().isoformat()
    filename = f'chat-room-{room.id}-{_clean_filename(room.title)}-{today}.csv'
    response = _csv_response(filename)
    writer = csv.writer(response)
    writer.writerow([
        'Message ID',
        'Room',
        'Room Type',
        'Sender',
        'Sender Username',
        'Message Type',
        'Body',
        'Attachment',
        'Reply To Message ID',
        'Edited',
        'Deleted',
        'Created At',
        'Updated At',
    ])
    messages_qs = room.messages.select_related('senderID', 'replyTo').order_by('datetime')
    for message in messages_qs:
        writer.writerow([
            message.id,
            room.title,
            room.get_roomType_display(),
            display_name(message.senderID) if message.senderID_id else 'System',
            message.senderID.username if message.senderID_id else '',
            message.get_messageType_display(),
            message.body or '',
            message.attachment.name if message.attachment else '',
            message.replyTo_id or '',
            'Yes' if message.isEdited else 'No',
            'Yes' if message.isDeleted else 'No',
            _local_datetime(message.datetime),
            _local_datetime(message.lastUpdatedOn),
        ])
    return response


@login_required
@check_groups('Admin', 'Owner')
@require_GET
def moderation_export(request):
    today = timezone.now().date().isoformat()
    filename = f'chat-moderation-reports-{today}.csv'
    response = _csv_response(filename)
    writer = csv.writer(response)
    writer.writerow([
        'Report ID',
        'Status',
        'Room',
        'Message ID',
        'Message Body',
        'Attachment',
        'Message Deleted',
        'Sender',
        'Sender Username',
        'Reported By',
        'Reason',
        'Reported At',
        'Reviewed By',
        'Updated At',
    ])
    for report in _moderation_scope_reports():
        message = report.messageID
        writer.writerow([
            report.id,
            report.get_status_display(),
            message.roomID.title if message and message.roomID_id else '',
            message.id if message else '',
            message.body if message else '',
            message.attachment.name if message and message.attachment else '',
            'Yes' if message and message.isDeleted else 'No',
            display_name(message.senderID) if message and message.senderID_id else 'System',
            message.senderID.username if message and message.senderID_id else '',
            display_name(report.reportedBy) if report.reportedBy_id else 'Unknown',
            report.reason or '',
            _local_datetime(report.datetime),
            display_name(report.reviewedBy) if report.reviewedBy_id else '',
            _local_datetime(report.lastUpdatedOn),
        ])
    return response


@login_required
@check_groups('Admin', 'Owner', 'Teaching', 'Student')
@require_GET
def unread_summary_api(request):
    return JsonResponse(_room_summary_payload(request.user))


def _moderation_scope_reports():
    return MessageReport.objects.select_related(
        'messageID',
        'messageID__roomID',
        'messageID__senderID',
        'reportedBy',
        'reviewedBy',
    ).order_by('-datetime')


@login_required
@check_groups('Admin', 'Owner')
def moderation(request):
    if request.method == 'POST':
        report = get_object_or_404(MessageReport, pk=request.POST.get('report_id'))
        action = request.POST.get('action')
        message_obj = report.messageID
        if action == 'reviewed':
            report.status = MessageReport.STATUS_REVIEWED
            report.reviewedBy = request.user
            report.save(update_fields=['status', 'reviewedBy', 'lastUpdatedOn'])
            messages.success(request, 'Report marked as reviewed.')
        elif action == 'dismissed':
            report.status = MessageReport.STATUS_DISMISSED
            report.reviewedBy = request.user
            report.save(update_fields=['status', 'reviewedBy', 'lastUpdatedOn'])
            messages.success(request, 'Report dismissed.')
        elif action == 'remove_message':
            message_obj.isDeleted = True
            message_obj.body = ''
            message_obj.save(update_fields=['isDeleted', 'body', 'lastUpdatedOn'])
            message_obj.roomID.save(update_fields=['lastUpdatedOn'])
            report.status = MessageReport.STATUS_REVIEWED
            report.reviewedBy = request.user
            report.save(update_fields=['status', 'reviewedBy', 'lastUpdatedOn'])
            messages.success(request, 'Message removed and report marked as reviewed.')
        elif action == 'mute_sender':
            if message_obj.senderID_id:
                participant = message_obj.roomID.participants.filter(userID=message_obj.senderID).first()
                if participant:
                    _mute_participant(participant, request.POST.get('mute_minutes'))
                    messages.success(request, 'Sender muted for the selected time period.')
                else:
                    messages.error(request, 'Sender is no longer a room participant.')
            report.status = MessageReport.STATUS_REVIEWED
            report.reviewedBy = request.user
            report.save(update_fields=['status', 'reviewedBy', 'lastUpdatedOn'])
        elif action == 'remove_and_mute':
            message_obj.isDeleted = True
            message_obj.body = ''
            message_obj.save(update_fields=['isDeleted', 'body', 'lastUpdatedOn'])
            message_obj.roomID.save(update_fields=['lastUpdatedOn'])
            if message_obj.senderID_id:
                participant = message_obj.roomID.participants.filter(userID=message_obj.senderID).first()
                if participant:
                    _mute_participant(participant, request.POST.get('mute_minutes'))
            report.status = MessageReport.STATUS_REVIEWED
            report.reviewedBy = request.user
            report.save(update_fields=['status', 'reviewedBy', 'lastUpdatedOn'])
            messages.success(request, 'Message removed and sender muted.')
        return redirect('chatApp:moderation')

    reports = _moderation_scope_reports().filter(status=MessageReport.STATUS_OPEN)
    reviewed_reports = _moderation_scope_reports().exclude(status=MessageReport.STATUS_OPEN)[:30]
    return render(request, 'chatApp/moderation.html', {
        'reports': reports,
        'reviewed_reports': reviewed_reports,
        'mute_duration_options': MUTE_DURATION_OPTIONS,
    })
