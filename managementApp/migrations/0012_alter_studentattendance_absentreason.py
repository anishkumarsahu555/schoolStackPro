# Generated by Django 4.2.8 on 2024-03-11 19:48

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('managementApp', '0011_studentattendance_isdeleted_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='studentattendance',
            name='absentReason',
            field=models.CharField(blank=True, default='', max_length=500, null=True),
        ),
    ]
