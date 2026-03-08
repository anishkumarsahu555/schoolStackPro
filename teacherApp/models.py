from django.contrib.auth.models import User
from django.db import models

from homeApp.models import SchoolDetail, SchoolSession
from managementApp.models import TeacherDetail, AssignSubjectsToTeacher, Standard, Subjects

# Create your models here.


class SubjectNote(models.Model):
    STATUS_CHOICES = (
        ('draft', 'Draft'),
        ('published', 'Published'),
    )

    schoolID = models.ForeignKey(SchoolDetail, blank=True, null=True, on_delete=models.CASCADE)
    sessionID = models.ForeignKey(SchoolSession, blank=True, null=True, on_delete=models.CASCADE)
    teacherID = models.ForeignKey(TeacherDetail, blank=True, null=True, on_delete=models.CASCADE)
    assignedSubjectTeacherID = models.ForeignKey(AssignSubjectsToTeacher, blank=True, null=True, on_delete=models.SET_NULL)
    standardID = models.ForeignKey(Standard, blank=True, null=True, on_delete=models.CASCADE)
    subjectID = models.ForeignKey(Subjects, blank=True, null=True, on_delete=models.CASCADE)
    title = models.CharField(max_length=500, blank=True, null=True)
    contentHtml = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    publishedAt = models.DateTimeField(blank=True, null=True)
    currentVersionNo = models.PositiveIntegerField(default=1)
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    isDeleted = models.BooleanField(default=False)
    lastEditedBy = models.CharField(max_length=500, blank=True, null=True)
    updatedByUserID = models.ForeignKey(User, blank=True, null=True, on_delete=models.SET_NULL, related_name='+')

    def __str__(self):
        return f'{self.title or "Untitled"} - {self.status}'

    class Meta:
        verbose_name_plural = 'a) Subject Notes'
        indexes = [
            models.Index(fields=['sessionID', 'teacherID', 'status', 'isDeleted'], name='tsn_sess_tchr_stat_idx'),
            models.Index(fields=['sessionID', 'subjectID', 'status', 'isDeleted'], name='tsn_sess_sub_stat_idx'),
            models.Index(fields=['sessionID', 'standardID', 'status', 'isDeleted'], name='tsn_sess_std_stat_idx'),
        ]


class SubjectNoteVersion(models.Model):
    noteID = models.ForeignKey(SubjectNote, on_delete=models.CASCADE)
    schoolID = models.ForeignKey(SchoolDetail, blank=True, null=True, on_delete=models.CASCADE)
    sessionID = models.ForeignKey(SchoolSession, blank=True, null=True, on_delete=models.CASCADE)
    teacherID = models.ForeignKey(TeacherDetail, blank=True, null=True, on_delete=models.CASCADE)
    title = models.CharField(max_length=500, blank=True, null=True)
    contentHtml = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=SubjectNote.STATUS_CHOICES, default='draft')
    versionNo = models.PositiveIntegerField(default=1)
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    isDeleted = models.BooleanField(default=False)
    lastEditedBy = models.CharField(max_length=500, blank=True, null=True)
    updatedByUserID = models.ForeignKey(User, blank=True, null=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        verbose_name_plural = 'b) Subject Note Versions'
        constraints = [
            models.UniqueConstraint(
                fields=['noteID', 'versionNo'],
                condition=models.Q(isDeleted=False),
                name='tsnv_note_version_unique_active'
            ),
        ]
        indexes = [
            models.Index(fields=['sessionID', 'noteID', 'isDeleted', 'datetime'], name='tsnv_sess_note_dt_idx'),
        ]
