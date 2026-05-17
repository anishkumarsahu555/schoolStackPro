from django.contrib import admin

from .models import ChatMessage, ChatParticipant, ChatPinnedMessage, ChatRoom, MessageReaction, MessageReadReceipt, MessageReport


@admin.register(ChatRoom)
class ChatRoomAdmin(admin.ModelAdmin):
    list_display = ('title', 'roomType', 'schoolID', 'sessionID', 'isReadOnly', 'isActive', 'lastUpdatedOn')
    list_filter = ('roomType', 'isReadOnly', 'isActive')
    search_fields = ('title',)


@admin.register(ChatParticipant)
class ChatParticipantAdmin(admin.ModelAdmin):
    list_display = ('roomID', 'userID', 'role', 'canPost', 'isMuted', 'mutedUntil', 'notificationLevel', 'notificationMuted', 'notificationMutedUntil', 'lastReadAt')
    list_filter = ('role', 'canPost', 'isMuted', 'notificationLevel', 'notificationMuted')
    search_fields = ('roomID__title', 'userID__username', 'userID__first_name', 'userID__last_name')


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ('roomID', 'senderID', 'messageType', 'datetime', 'isEdited', 'isDeleted')
    list_filter = ('messageType', 'isEdited', 'isDeleted')
    search_fields = ('body', 'roomID__title', 'senderID__username')


admin.site.register(MessageReadReceipt)
admin.site.register(MessageReaction)
admin.site.register(ChatPinnedMessage)
admin.site.register(MessageReport)
