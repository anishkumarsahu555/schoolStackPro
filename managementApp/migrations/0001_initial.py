# Generated by Django 4.2.8 on 2024-03-04 12:20

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import dynamic_filenames
import stdimage.models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('homeApp', '0004_schooldetail_isdeleted_schoolowner_isdeleted_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='TeacherDetail',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('firstName', models.CharField(blank=True, max_length=500, null=True)),
                ('middleName', models.CharField(blank=True, max_length=500, null=True)),
                ('lastName', models.CharField(blank=True, max_length=500, null=True)),
                ('DOB', models.DateField(blank=True, null=True)),
                ('aadhar', models.CharField(blank=True, max_length=500, null=True)),
                ('gender', models.CharField(blank=True, max_length=500, null=True)),
                ('bloodGroup', models.CharField(blank=True, max_length=500, null=True)),
                ('presentAddress', models.TextField(blank=True, null=True)),
                ('presentPinCode', models.CharField(blank=True, max_length=500, null=True)),
                ('presentCity', models.CharField(blank=True, max_length=500, null=True)),
                ('presentState', models.CharField(blank=True, max_length=500, null=True)),
                ('presentCountry', models.CharField(blank=True, max_length=500, null=True)),
                ('permanentAddress', models.TextField(blank=True, null=True)),
                ('permanentPinCode', models.CharField(blank=True, max_length=500, null=True)),
                ('permanentCity', models.CharField(blank=True, max_length=500, null=True)),
                ('permanentState', models.CharField(blank=True, max_length=500, null=True)),
                ('permanentCountry', models.CharField(blank=True, max_length=500, null=True)),
                ('phoneNumber', models.CharField(blank=True, max_length=15, null=True)),
                ('email', models.CharField(blank=True, max_length=500, null=True)),
                ('photo', stdimage.models.StdImageField(blank=True, force_min_size=False, upload_to=dynamic_filenames.FilePattern(filename_pattern='my_model/{app_label:.25}/{model_name:.30}/{uuid:base32}{ext}'), variations={'medium': (250, 250), 'thumbnail': (100, 100, True)})),
                ('username', models.CharField(blank=True, max_length=500, null=True)),
                ('password', models.CharField(blank=True, max_length=500, null=True)),
                ('dateOfJoining', models.DateField(blank=True, null=True)),
                ('dateOfLeaving', models.DateField(blank=True, null=True)),
                ('currentPosition', models.CharField(blank=True, max_length=500, null=True)),
                ('EmployeeCode', models.CharField(blank=True, max_length=500, null=True)),
                ('qualification', models.CharField(blank=True, max_length=500, null=True)),
                ('datetime', models.DateTimeField(auto_now_add=True)),
                ('lastUpdatedOn', models.DateTimeField(auto_now=True)),
                ('isActive', models.BooleanField(default=True)),
                ('isDeleted', models.BooleanField(default=False)),
                ('lastEditedBy', models.CharField(blank=True, max_length=500, null=True)),
                ('schoolID', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='homeApp.schooldetail')),
                ('sessionID', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='homeApp.schoolsession')),
                ('userID', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name_plural': 'g) Teachers Details',
            },
        ),
        migrations.CreateModel(
            name='Subjects',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(blank=True, max_length=500, null=True)),
                ('datetime', models.DateTimeField(auto_now_add=True)),
                ('lastUpdatedOn', models.DateTimeField(auto_now=True)),
                ('isDeleted', models.BooleanField(default=False)),
                ('lastEditedBy', models.CharField(blank=True, max_length=500, null=True)),
                ('schoolID', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='homeApp.schooldetail')),
                ('sessionID', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='homeApp.schoolsession')),
            ],
            options={
                'verbose_name_plural': 'i) Subject Details',
            },
        ),
        migrations.CreateModel(
            name='Standard',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(blank=True, max_length=500, null=True)),
                ('classLocation', models.CharField(blank=True, default='No Data', max_length=500, null=True)),
                ('hasSection', models.CharField(blank=True, max_length=500, null=True)),
                ('startingRoll', models.CharField(blank=True, max_length=500, null=True)),
                ('endingRoll', models.CharField(blank=True, max_length=500, null=True)),
                ('section', models.CharField(blank=True, max_length=500, null=True)),
                ('datetime', models.DateTimeField(auto_now_add=True)),
                ('lastUpdatedOn', models.DateTimeField(auto_now=True)),
                ('isDeleted', models.BooleanField(default=False)),
                ('lastEditedBy', models.CharField(blank=True, max_length=500, null=True)),
                ('classTeacher', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='managementApp.teacherdetail')),
                ('schoolID', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='homeApp.schooldetail')),
                ('sessionID', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='homeApp.schoolsession')),
            ],
            options={
                'verbose_name_plural': 'h) Standard Details',
            },
        ),
        migrations.CreateModel(
            name='NonTeachingStaff',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('firstName', models.CharField(blank=True, max_length=500, null=True)),
                ('middleName', models.CharField(blank=True, max_length=500, null=True)),
                ('lastName', models.CharField(blank=True, max_length=500, null=True)),
                ('phoneNumber', models.CharField(blank=True, max_length=15, null=True)),
                ('email', models.CharField(blank=True, max_length=500, null=True)),
                ('address', models.TextField(blank=True, null=True)),
                ('city', models.CharField(blank=True, max_length=500, null=True)),
                ('pinCode', models.CharField(blank=True, max_length=500, null=True)),
                ('state', models.CharField(blank=True, max_length=500, null=True)),
                ('country', models.CharField(blank=True, max_length=500, null=True)),
                ('aadhar', models.CharField(blank=True, max_length=500, null=True)),
                ('DOB', models.DateField(blank=True, null=True)),
                ('gender', models.CharField(blank=True, max_length=500, null=True)),
                ('bloodGroup', models.CharField(blank=True, max_length=500, null=True)),
                ('EmployeeCode', models.CharField(blank=True, max_length=500, null=True)),
                ('currentPosition', models.CharField(blank=True, max_length=500, null=True)),
                ('qualification', models.CharField(blank=True, max_length=500, null=True)),
                ('joinDate', models.DateField(blank=True, null=True)),
                ('releaveDate', models.DateField(blank=True, null=True)),
                ('username', models.CharField(blank=True, max_length=500, null=True)),
                ('password', models.CharField(blank=True, max_length=500, null=True)),
                ('photo', stdimage.models.StdImageField(blank=True, force_min_size=False, upload_to=dynamic_filenames.FilePattern(filename_pattern='my_model/{app_label:.25}/{model_name:.30}/{uuid:base32}{ext}'), variations={'medium': (250, 250), 'thumbnail': (100, 100, True)})),
                ('datetime', models.DateTimeField(auto_now_add=True)),
                ('lastUpdatedOn', models.DateTimeField(auto_now=True)),
                ('isActive', models.BooleanField(default=True)),
                ('isDeleted', models.BooleanField(default=False)),
                ('lastEditedBy', models.CharField(blank=True, max_length=500, null=True)),
                ('schoolID', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='homeApp.schooldetail')),
                ('sessionID', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='homeApp.schoolsession')),
                ('userID', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name_plural': 'f) Non- Teaching Staff.',
            },
        ),
        migrations.CreateModel(
            name='ComputerOperator',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('firstName', models.CharField(blank=True, max_length=500, null=True)),
                ('middleName', models.CharField(blank=True, max_length=500, null=True)),
                ('lastName', models.CharField(blank=True, max_length=500, null=True)),
                ('phoneNumber', models.CharField(blank=True, max_length=15, null=True)),
                ('email', models.CharField(blank=True, max_length=500, null=True)),
                ('address', models.TextField(blank=True, null=True)),
                ('city', models.CharField(blank=True, max_length=500, null=True)),
                ('pinCode', models.CharField(blank=True, max_length=500, null=True)),
                ('state', models.CharField(blank=True, max_length=500, null=True)),
                ('country', models.CharField(blank=True, max_length=500, null=True)),
                ('aadhar', models.CharField(blank=True, max_length=500, null=True)),
                ('DOB', models.DateField(blank=True, null=True)),
                ('qualification', models.CharField(blank=True, max_length=500, null=True)),
                ('joinDate', models.DateField(blank=True, null=True)),
                ('releaveDate', models.DateField(blank=True, null=True)),
                ('username', models.CharField(blank=True, max_length=500, null=True)),
                ('password', models.CharField(blank=True, max_length=500, null=True)),
                ('photo', stdimage.models.StdImageField(blank=True, force_min_size=False, upload_to=dynamic_filenames.FilePattern(filename_pattern='my_model/{app_label:.25}/{model_name:.30}/{uuid:base32}{ext}'), variations={'medium': (250, 250), 'thumbnail': (100, 100, True)})),
                ('datetime', models.DateTimeField(auto_now_add=True)),
                ('lastUpdatedOn', models.DateTimeField(auto_now=True)),
                ('isActive', models.BooleanField(default=True)),
                ('isDeleted', models.BooleanField(default=False)),
                ('lastEditedBy', models.CharField(blank=True, max_length=500, null=True)),
                ('schoolID', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='homeApp.schooldetail')),
                ('sessionID', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='homeApp.schoolsession')),
                ('userID', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name_plural': 'e) Computer Operator.',
            },
        ),
    ]