from django.contrib.auth.models import User
from django.db import models

from homeApp.models import SchoolDetail, SchoolSession
from managementApp.models import Standard, Subjects
from utils.utils import UPLOAD_TO_PATTERNS


class ChatRoom(models.Model):
    ROOM_TYPE_DIRECT = 'direct'
    ROOM_TYPE_CLASS = 'class'
    ROOM_TYPE_SUBJECT = 'subject'
    ROOM_TYPE_ANNOUNCEMENT = 'announcement'
    ROOM_TYPE_SUPPORT = 'support'
    ROOM_TYPE_CHOICES = (
        (ROOM_TYPE_DIRECT, 'Direct Message'),
        (ROOM_TYPE_CLASS, 'Class Group'),
        (ROOM_TYPE_SUBJECT, 'Subject Group'),
        (ROOM_TYPE_ANNOUNCEMENT, 'Announcement'),
        (ROOM_TYPE_SUPPORT, 'Support Thread'),
    )

    title = models.CharField(max_length=255)
    roomType = models.CharField(max_length=30, choices=ROOM_TYPE_CHOICES, default=ROOM_TYPE_DIRECT)
    schoolID = models.ForeignKey(SchoolDetail, blank=True, null=True, on_delete=models.CASCADE)
    sessionID = models.ForeignKey(SchoolSession, blank=True, null=True, on_delete=models.CASCADE)
    standardID = models.ForeignKey(Standard, blank=True, null=True, on_delete=models.SET_NULL)
    subjectID = models.ForeignKey(Subjects, blank=True, null=True, on_delete=models.SET_NULL)
    createdBy = models.ForeignKey(User, blank=True, null=True, on_delete=models.SET_NULL, related_name='created_chat_rooms')
    isReadOnly = models.BooleanField(default=False)
    isActive = models.BooleanField(default=True)
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)

    def __str__(self):
        return self.title

    class Meta:
        verbose_name_plural = 'a) Chat Rooms'
        indexes = [
            models.Index(fields=['schoolID', 'sessionID', 'roomType', 'isActive'], name='chat_room_scope_idx'),
            models.Index(fields=['lastUpdatedOn'], name='chat_room_updated_idx'),
        ]


class ChatParticipant(models.Model):
    ROLE_ADMIN = 'admin'
    ROLE_TEACHER = 'teacher'
    ROLE_STUDENT = 'student'
    ROLE_PARENT = 'parent'
    ROLE_CHOICES = (
        (ROLE_ADMIN, 'Admin'),
        (ROLE_TEACHER, 'Teacher'),
        (ROLE_STUDENT, 'Student'),
        (ROLE_PARENT, 'Parent'),
    )
    NOTIFY_ALL = 'all'
    NOTIFY_MENTIONS = 'mentions'
    NOTIFY_OFF = 'off'
    NOTIFICATION_LEVEL_CHOICES = (
        (NOTIFY_ALL, 'All messages'),
        (NOTIFY_MENTIONS, 'Mentions only'),
        (NOTIFY_OFF, 'Off'),
    )

    roomID = models.ForeignKey(ChatRoom, on_delete=models.CASCADE, related_name='participants')
    userID = models.ForeignKey(User, on_delete=models.CASCADE, related_name='chat_participations')
    role = models.CharField(max_length=30, choices=ROLE_CHOICES)
    canPost = models.BooleanField(default=True)
    isMuted = models.BooleanField(default=False)
    mutedUntil = models.DateTimeField(blank=True, null=True)
    notificationMuted = models.BooleanField(default=False)
    notificationLevel = models.CharField(max_length=20, choices=NOTIFICATION_LEVEL_CHOICES, default=NOTIFY_ALL)
    notificationMutedUntil = models.DateTimeField(blank=True, null=True)
    lastReadMessage = models.ForeignKey('ChatMessage', blank=True, null=True, on_delete=models.SET_NULL, related_name='+')
    lastReadAt = models.DateTimeField(blank=True, null=True)
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)

    def __str__(self):
        return f'{self.userID} in {self.roomID}'

    class Meta:
        verbose_name_plural = 'b) Chat Participants'
        constraints = [
            models.UniqueConstraint(fields=['roomID', 'userID'], name='unique_chat_room_participant'),
        ]
        indexes = [
            models.Index(fields=['userID', 'roomID'], name='chat_part_user_room_idx'),
            models.Index(fields=['roomID', 'role'], name='chat_part_room_role_idx'),
        ]


class ChatMessage(models.Model):
    MESSAGE_TEXT = 'text'
    MESSAGE_ATTACHMENT = 'attachment'
    MESSAGE_VOICE = 'voice'
    MESSAGE_SYSTEM = 'system'
    MESSAGE_TYPE_CHOICES = (
        (MESSAGE_TEXT, 'Text'),
        (MESSAGE_ATTACHMENT, 'Attachment'),
        (MESSAGE_VOICE, 'Voice Note'),
        (MESSAGE_SYSTEM, 'System'),
    )

    roomID = models.ForeignKey(ChatRoom, on_delete=models.CASCADE, related_name='messages')
    senderID = models.ForeignKey(User, blank=True, null=True, on_delete=models.SET_NULL, related_name='sent_chat_messages')
    messageType = models.CharField(max_length=30, choices=MESSAGE_TYPE_CHOICES, default=MESSAGE_TEXT)
    body = models.TextField(blank=True, null=True)
    attachment = models.FileField(upload_to=UPLOAD_TO_PATTERNS, blank=True, null=True)
    replyTo = models.ForeignKey('self', blank=True, null=True, on_delete=models.SET_NULL, related_name='replies')
    isEdited = models.BooleanField(default=False)
    isDeleted = models.BooleanField(default=False)
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)

    def __str__(self):
        return self.body[:80] if self.body else f'Message #{self.pk}'

    class Meta:
        verbose_name_plural = 'c) Chat Messages'
        indexes = [
            models.Index(fields=['roomID', 'isDeleted', 'datetime'], name='chat_msg_room_time_idx'),
            models.Index(fields=['senderID', 'datetime'], name='chat_msg_sender_time_idx'),
        ]


class MessageReadReceipt(models.Model):
    messageID = models.ForeignKey(ChatMessage, on_delete=models.CASCADE, related_name='readReceipts')
    userID = models.ForeignKey(User, on_delete=models.CASCADE, related_name='chat_read_receipts')
    readAt = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = 'd) Message Read Receipts'
        constraints = [
            models.UniqueConstraint(fields=['messageID', 'userID'], name='unique_chat_message_read_receipt'),
        ]
        indexes = [
            models.Index(fields=['userID', 'readAt'], name='chat_read_user_time_idx'),
        ]


class MessageReaction(models.Model):
    REACTION_ACK = 'ack'
    REACTION_LIKE = 'like'
    REACTION_QUESTION = 'question'
    REACTION_CHOICES = (
        (REACTION_ACK, 'Acknowledged'),
        (REACTION_LIKE, 'Useful'),
        (REACTION_QUESTION, 'Question'),
    )

    messageID = models.ForeignKey(ChatMessage, on_delete=models.CASCADE, related_name='reactions')
    userID = models.ForeignKey(User, on_delete=models.CASCADE, related_name='chat_message_reactions')
    reactionType = models.CharField(max_length=30, choices=REACTION_CHOICES)
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)

    def __str__(self):
        return f'{self.userID} {self.reactionType} on {self.messageID_id}'

    class Meta:
        verbose_name_plural = 'e) Message Reactions'
        constraints = [
            models.UniqueConstraint(fields=['messageID', 'userID', 'reactionType'], name='unique_chat_message_reaction'),
        ]
        indexes = [
            models.Index(fields=['messageID', 'reactionType'], name='chat_react_msg_type_idx'),
            models.Index(fields=['userID', 'datetime'], name='chat_react_user_time_idx'),
        ]


class ChatPinnedMessage(models.Model):
    roomID = models.ForeignKey(ChatRoom, on_delete=models.CASCADE, related_name='pinnedMessages')
    messageID = models.ForeignKey(ChatMessage, on_delete=models.CASCADE, related_name='pins')
    pinnedBy = models.ForeignKey(User, blank=True, null=True, on_delete=models.SET_NULL, related_name='pinned_chat_messages')
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)

    def __str__(self):
        return f'{self.roomID} pinned message #{self.messageID_id}'

    class Meta:
        verbose_name_plural = 'f) Pinned Messages'
        constraints = [
            models.UniqueConstraint(fields=['roomID', 'messageID'], name='unique_chat_room_pinned_message'),
        ]
        indexes = [
            models.Index(fields=['roomID', 'datetime'], name='chat_pin_room_time_idx'),
        ]


class ChatSavedMessage(models.Model):
    messageID = models.ForeignKey(ChatMessage, on_delete=models.CASCADE, related_name='savedBy')
    userID = models.ForeignKey(User, on_delete=models.CASCADE, related_name='saved_chat_messages')
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)

    def __str__(self):
        return f'{self.userID} saved message #{self.messageID_id}'

    class Meta:
        verbose_name_plural = 'g) Saved Messages'
        constraints = [
            models.UniqueConstraint(fields=['messageID', 'userID'], name='unique_chat_saved_message'),
        ]
        indexes = [
            models.Index(fields=['userID', 'datetime'], name='chat_saved_user_time_idx'),
        ]


class MessageReport(models.Model):
    STATUS_OPEN = 'open'
    STATUS_REVIEWED = 'reviewed'
    STATUS_DISMISSED = 'dismissed'
    STATUS_CHOICES = (
        (STATUS_OPEN, 'Open'),
        (STATUS_REVIEWED, 'Reviewed'),
        (STATUS_DISMISSED, 'Dismissed'),
    )

    messageID = models.ForeignKey(ChatMessage, on_delete=models.CASCADE, related_name='reports')
    reportedBy = models.ForeignKey(User, blank=True, null=True, on_delete=models.SET_NULL, related_name='chat_message_reports')
    reason = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_OPEN)
    reviewedBy = models.ForeignKey(User, blank=True, null=True, on_delete=models.SET_NULL, related_name='reviewed_chat_reports')
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)

    class Meta:
        verbose_name_plural = 'g) Message Reports'
        indexes = [
            models.Index(fields=['status', 'datetime'], name='chat_report_status_idx'),
        ]
