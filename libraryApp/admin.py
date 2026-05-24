from django.contrib import admin

from libraryApp.models import (
    LibraryAuthor,
    LibraryBook,
    LibraryBookAuthor,
    LibraryBookCopy,
    LibraryCategory,
    LibraryFine,
    LibraryIssue,
    LibraryMember,
    LibraryMemberCardDesign,
    LibraryPublisher,
    LibraryReservation,
    LibrarySetting,
)


class LibraryBookAuthorInline(admin.TabularInline):
    model = LibraryBookAuthor
    extra = 1


@admin.register(LibraryBook)
class LibraryBookAdmin(admin.ModelAdmin):
    list_display = ('title', 'isbn', 'category', 'publisher', 'isActive', 'isDeleted', 'lastUpdatedOn')
    search_fields = ('title', 'isbn', 'shelfLocation')
    list_filter = ('isActive', 'isDeleted', 'category', 'publisher')
    inlines = [LibraryBookAuthorInline]


@admin.register(LibraryBookCopy)
class LibraryBookCopyAdmin(admin.ModelAdmin):
    list_display = ('accessionNumber', 'book', 'status', 'condition', 'isActive', 'isDeleted')
    search_fields = ('accessionNumber', 'barcodeValue', 'qrCodeValue', 'book__title')
    list_filter = ('status', 'condition', 'isActive', 'isDeleted')


@admin.register(LibraryMember)
class LibraryMemberAdmin(admin.ModelAdmin):
    list_display = ('memberCode', 'memberType', 'display_name', 'maxBooksAllowed', 'isActive', 'isDeleted')
    search_fields = ('memberCode', 'student__name', 'staff__name')
    list_filter = ('memberType', 'isActive', 'isDeleted')


@admin.register(LibraryIssue)
class LibraryIssueAdmin(admin.ModelAdmin):
    list_display = ('copy', 'member', 'issueDate', 'dueDate', 'returnDate', 'status', 'fineAmount')
    search_fields = ('copy__accessionNumber', 'copy__book__title', 'member__memberCode')
    list_filter = ('status', 'isDeleted')


@admin.register(LibraryFine)
class LibraryFineAdmin(admin.ModelAdmin):
    list_display = ('member', 'reason', 'amount', 'paidAmount', 'status', 'isDeleted')
    search_fields = ('member__memberCode', 'member__student__name', 'member__staff__name')
    list_filter = ('reason', 'status', 'isDeleted')


admin.site.register(LibraryCategory)
admin.site.register(LibraryAuthor)
admin.site.register(LibraryPublisher)
admin.site.register(LibraryReservation)
admin.site.register(LibrarySetting)
admin.site.register(LibraryMemberCardDesign)
