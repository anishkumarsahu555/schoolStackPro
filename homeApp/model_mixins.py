from django.contrib.contenttypes.fields import GenericRelation
from django.db import models


class TimeStampedModel(models.Model):
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)

    class Meta:
        abstract = True


class SoftDeleteModel(models.Model):
    isDeleted = models.BooleanField(default=False)

    class Meta:
        abstract = True


class AuditedModel(models.Model):
    audit_logs = GenericRelation(
        'homeApp.AuditLog',
        content_type_field='content_type',
        object_id_field='object_id',
        related_query_name='audited_object',
    )

    class Meta:
        abstract = True


class SchoolScopedModel(TimeStampedModel, SoftDeleteModel, AuditedModel):
    schoolID = models.ForeignKey('homeApp.SchoolDetail', blank=True, null=True, on_delete=models.CASCADE)
    sessionID = models.ForeignKey('homeApp.SchoolSession', blank=True, null=True, on_delete=models.CASCADE)

    class Meta:
        abstract = True
