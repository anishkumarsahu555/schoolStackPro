# Generated by Django 4.2.8 on 2023-12-31 21:08

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('schoolApp', '0001_initial'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='section',
            name='schoolID',
        ),
        migrations.RemoveField(
            model_name='section',
            name='sessionID',
        ),
        migrations.RemoveField(
            model_name='section',
            name='standardID',
        ),
        migrations.AddField(
            model_name='standard',
            name='classTeacher',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='schoolApp.teacherdetail'),
        ),
        migrations.AddField(
            model_name='standard',
            name='section',
            field=models.CharField(blank=True, max_length=500, null=True),
        ),
        migrations.DeleteModel(
            name='AssignTeacherToClassOrSection',
        ),
        migrations.DeleteModel(
            name='Section',
        ),
    ]
