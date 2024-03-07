# Generated by Django 4.2.8 on 2024-03-06 19:54

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('managementApp', '0004_remove_nonteachingstaff_schoolid_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='teacherdetail',
            name='salary',
            field=models.FloatField(default=0.0),
        ),
        migrations.AlterField(
            model_name='teacherdetail',
            name='isActive',
            field=models.CharField(blank=True, default='Yes', max_length=200, null=True),
        ),
    ]