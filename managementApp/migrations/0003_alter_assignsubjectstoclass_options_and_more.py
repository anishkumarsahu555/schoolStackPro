# Generated by Django 4.2.8 on 2024-03-05 15:57

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('managementApp', '0002_assignsubjectstoclass'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='assignsubjectstoclass',
            options={'verbose_name_plural': 'j) Assign Subjects To Class Details'},
        ),
        migrations.RenameField(
            model_name='assignsubjectstoclass',
            old_name='standard',
            new_name='standardID',
        ),
    ]
