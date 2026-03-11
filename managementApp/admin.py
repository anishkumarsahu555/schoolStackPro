from django.contrib import admin
from django.db import models

from .models import (
    AssignExamToClass,
    AssignSubjectsToClass,
    AssignSubjectsToTeacher,
    Event,
    EventType,
    Exam,
    ExamTimeTable,
    LeaveActionLog,
    LeaveApplication,
    LeaveType,
    MarkOfStudentsByExam,
    Parent,
    Standard,
    Student,
    StudentAttendance,
    StudentFee,
    StudentIdCardRecord,
    Subjects,
    TeacherAttendance,
    TeacherDetail,
)


def _has_field(model, field_name):
    return any(f.name == field_name for f in model._meta.get_fields())


def _unique(values):
    result = []
    seen = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return tuple(result)


class AllFieldsAdmin(admin.ModelAdmin):
    list_per_page = 50

    def get_list_display(self, request):
        # Show all concrete fields exactly as requested.
        return tuple(field.name for field in self.model._meta.concrete_fields)

    def get_list_filter(self, request):
        model = self.model
        preferred = ['schoolID', 'sessionID', 'isDeleted', 'isCurrent', 'status', 'isActive', 'isPaid', 'actionType']
        return _unique([field for field in preferred if _has_field(model, field)])

    def get_search_fields(self, request):
        text_fields = []
        for field in self.model._meta.concrete_fields:
            if isinstance(field, (models.CharField, models.TextField, models.EmailField)):
                text_fields.append(field.name)
        return _unique(['=id'] + text_fields[:12])

    def get_readonly_fields(self, request, obj=None):
        model = self.model
        return _unique([
            'datetime' if _has_field(model, 'datetime') else None,
            'lastUpdatedOn' if _has_field(model, 'lastUpdatedOn') else None,
        ])


MODELS = [
    TeacherDetail,
    Standard,
    Student,
    Parent,
    Subjects,
    AssignSubjectsToClass,
    AssignSubjectsToTeacher,
    Exam,
    AssignExamToClass,
    ExamTimeTable,
    StudentAttendance,
    TeacherAttendance,
    StudentFee,
    MarkOfStudentsByExam,
    EventType,
    Event,
    StudentIdCardRecord,
    LeaveType,
    LeaveApplication,
    LeaveActionLog,
]

for model in MODELS:
    admin.site.register(model, AllFieldsAdmin)
