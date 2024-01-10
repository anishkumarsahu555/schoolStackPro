from django.contrib import admin
from .models import *

# Register your models here.

class NonTeachingStaffAdmin(admin.ModelAdmin):
    search_fields = ['firstName', 'phoneNumber', 'EmployeeCode']

    list_display = ['firstName', 'phoneNumber', 'EmployeeCode', 'isActive', 'datetime', 'lastUpdatedOn', ]


admin.site.register(NonTeachingStaff, NonTeachingStaffAdmin)


class TeacherDetailAdmin(admin.ModelAdmin):
    search_fields = ['firstName', 'phoneNumber', 'EmployeeCode']

    list_display = ['firstName', 'phoneNumber', 'EmployeeCode', 'isActive', 'datetime', 'lastUpdatedOn', ]


admin.site.register(TeacherDetail, TeacherDetailAdmin)


class StandardAdmin(admin.ModelAdmin):
    search_fields = ['name', 'classLocation', 'hasSection', 'section', 'classTeacher']

    list_display = ['name', 'sessionID', 'classLocation', 'hasSection', 'startingRoll', 'endingRoll', 'datetime',
                    'lastUpdatedOn', ]


admin.site.register(Standard, StandardAdmin)

#
# class SectionAdmin(admin.ModelAdmin):
#     search_fields = ['name']
#
#     list_display = ['name', 'standardID', 'sessionID', 'startingRoll', 'endingRoll', 'datetime', 'lastUpdatedOn', ]
#
#
# admin.site.register(Section, SectionAdmin)
#
#
# class AssignTeacherToClassOrSectionAdmin(admin.ModelAdmin):
#     list_display = ['classTeacher', 'standardID', 'sessionID', 'datetime', 'lastUpdatedOn', ]
#
#
# admin.site.register(AssignTeacherToClassOrSection, AssignTeacherToClassOrSectionAdmin)
#
