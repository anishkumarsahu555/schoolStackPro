import os
import runpy
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch


class EnvironmentSettingsTests(TestCase):
    def load_settings(self, contents, **exported):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            module = root / 'schoolStackPro' / 'settings.py'
            module.parent.mkdir()
            shutil.copyfile(Path(__file__).with_name('settings.py'), module)
            (root / '.env').write_text(contents)
            with patch.dict(os.environ, exported, clear=True):
                return runpy.run_path(str(module))

    def test_root_file_and_exported_overrides(self):
        settings = self.load_settings(
            'db=mysql\nDB_NAME=example\nREDIS_URL=redis://cache:6379/2\n'
            'CSRF_TRUSTED_ORIGINS=https://example.org, https://staff.example.org\n'
            'VAPID_PRIVATE_KEY="sample key"\nWRITE_LOG_ON_FILE=no\n',
            DB_NAME='exported',
        )
        database = settings['DATABASES']['default']
        self.assertEqual(database['ENGINE'], 'django.db.backends.mysql')
        self.assertEqual(database['NAME'], 'exported')
        self.assertEqual(settings['CACHES']['default']['LOCATION'], 'redis://cache:6379/2')
        self.assertEqual(settings['CSRF_TRUSTED_ORIGINS'], ['https://example.org', 'https://staff.example.org'])
        self.assertEqual(settings['VAPID_PRIVATE_KEY'], 'sample key')
        self.assertFalse(settings['WRITE_LOG_ON_FILE'])

    def test_database_engine_aliases_and_exported_precedence(self):
        settings = self.load_settings('DB_ENGINE=postgres\n')
        self.assertEqual(settings['DATABASES']['default']['ENGINE'], 'django.db.backends.postgresql')
        settings = self.load_settings('DB_ENGINE=postgres\n', db='sqlite')
        self.assertEqual(settings['DATABASES']['default']['ENGINE'], 'django.db.backends.sqlite3')
        settings = self.load_settings('db=sqlite\n', DB_ENGINE='postgres')
        self.assertEqual(settings['DATABASES']['default']['ENGINE'], 'django.db.backends.postgresql')

    def test_launcher_selects_environment_file(self):
        with TemporaryDirectory() as directory:
            selected = Path(directory) / 'staging.env'
            selected.write_text('db=postgres\nDB_NAME=staging\n')
            settings = self.load_settings(
                'db=sqlite\nDB_NAME=local\n', SCHOOLSTACK_ENV_FILE=str(selected),
            )
            self.assertEqual(settings['DATABASES']['default']['NAME'], 'staging')
            self.assertEqual(settings['DATABASES']['default']['ENGINE'], 'django.db.backends.postgresql')

    def test_defaults_and_explicit_empty_origins(self):
        settings = self.load_settings('')
        self.assertEqual(settings['DATABASES']['default']['NAME'], settings['BASE_DIR'] / 'db.sqlite3')
        self.assertEqual(settings['CSRF_TRUSTED_ORIGINS'], ['https://schoolsstack.in', 'https://www.schoolsstack.in'])
        settings = self.load_settings('db=postgres\nDB_NAME=example\nCSRF_TRUSTED_ORIGINS=\n')
        self.assertEqual(settings['DATABASES']['default']['ENGINE'], 'django.db.backends.postgresql')
        self.assertEqual(settings['DATABASES']['default']['NAME'], 'example')
        self.assertEqual(settings['CSRF_TRUSTED_ORIGINS'], [])
