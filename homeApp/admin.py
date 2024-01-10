from django.contrib import admin
from .models import *


# Register your models here.

class SchoolOwnerAdmin(admin.ModelAdmin):
    list_display = ['name', 'email', 'password', 'phoneNumber', 'username', 'datetime', 'lastUpdatedOn', ]


admin.site.register(SchoolOwner, SchoolOwnerAdmin)


class SchoolDetailAdmin(admin.ModelAdmin):
    list_display = ['name', 'address', 'city', 'phoneNumber', 'email', 'datetime', 'lastUpdatedOn', ]


admin.site.register(SchoolDetail, SchoolDetailAdmin)


class SchoolSocialLinkAdmin(admin.ModelAdmin):
    list_display = ['schoolID', 'facebook', 'twitter', 'googlePlus', 'datetime', 'lastUpdatedOn', ]


admin.site.register(SchoolSocialLink, SchoolSocialLinkAdmin)


class SchoolSessionAdmin(admin.ModelAdmin):
    list_display = ['sessionYear', 'isCurrent', 'datetime', 'lastUpdatedOn', ]


admin.site.register(SchoolSession, SchoolSessionAdmin)
