from django.contrib import admin

from teacherApp.models import SubjectNote, SubjectNoteVersion


def _all_concrete_fields(model):
    return tuple(field.name for field in model._meta.concrete_fields)


class AllFieldsAdmin(admin.ModelAdmin):
    list_per_page = 50

    def get_list_display(self, request):
        return _all_concrete_fields(self.model)


@admin.register(SubjectNote)
class SubjectNoteAdmin(AllFieldsAdmin):
    list_filter = ('schoolID', 'sessionID', 'status', 'isDeleted')
    search_fields = ('=id', 'title', 'teacherID__name', 'subjectID__name', 'standardID__name')
    readonly_fields = ('datetime', 'lastUpdatedOn')


@admin.register(SubjectNoteVersion)
class SubjectNoteVersionAdmin(AllFieldsAdmin):
    list_filter = ('schoolID', 'sessionID', 'status', 'isDeleted')
    search_fields = ('=id', 'title', 'noteID__title', 'teacherID__name')
    readonly_fields = ('datetime', 'lastUpdatedOn')
