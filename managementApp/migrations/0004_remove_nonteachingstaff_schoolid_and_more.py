# Generated by Django 4.2.8 on 2024-03-06 13:28

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('managementApp', '0003_alter_assignsubjectstoclass_options_and_more'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='nonteachingstaff',
            name='schoolID',
        ),
        migrations.RemoveField(
            model_name='nonteachingstaff',
            name='sessionID',
        ),
        migrations.RemoveField(
            model_name='nonteachingstaff',
            name='userID',
        ),
        migrations.RenameField(
            model_name='teacherdetail',
            old_name='DOB',
            new_name='dob',
        ),
        migrations.RenameField(
            model_name='teacherdetail',
            old_name='EmployeeCode',
            new_name='employeeCode',
        ),
        migrations.RenameField(
            model_name='teacherdetail',
            old_name='firstName',
            new_name='name',
        ),
        migrations.RenameField(
            model_name='teacherdetail',
            old_name='lastName',
            new_name='staffType',
        ),
        migrations.RemoveField(
            model_name='teacherdetail',
            name='middleName',
        ),
        migrations.AddField(
            model_name='teacherdetail',
            name='additionalDetails',
            field=models.TextField(blank=True, null=True),
        ),
        migrations.DeleteModel(
            name='ComputerOperator',
        ),
        migrations.DeleteModel(
            name='NonTeachingStaff',
        ),
    ]