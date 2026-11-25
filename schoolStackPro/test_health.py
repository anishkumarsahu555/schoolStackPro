"""Readiness contracts, including database failure and HTTPS deployments."""
from unittest.mock import patch

from django.db import OperationalError
from django.test import SimpleTestCase, override_settings


@override_settings(DEBUG=False, ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=True)
class HealthTests(SimpleTestCase):
    @patch('schoolStackPro.health.connection')
    def test_liveness_is_independent_of_database(self, connection):
        connection.cursor.side_effect = OperationalError('database offline')
        response = self.client.get('/livez')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(self.client.head('/livez').status_code, 200)
        self.assertEqual(self.client.post('/livez').status_code, 405)
        connection.cursor.assert_not_called()

    @patch('schoolStackPro.health.connection')
    def test_ready_over_http_and_not_cached(self, connection):
        cursor = connection.cursor
        cursor.return_value.__enter__.return_value.fetchone.return_value = (1,)
        response = self.client.get('/healthz')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})
        self.assertIn('no-store', response['Cache-Control'])
        cursor.return_value.__enter__.return_value.execute.assert_called_once_with('SELECT 1')
        self.assertEqual(self.client.head('/healthz').status_code, 200)
        self.assertEqual(self.client.get('/login/').status_code, 301)

    @patch('schoolStackPro.health.connection')
    def test_database_failure_is_safe(self, connection):
        connection.cursor.side_effect = OperationalError('private credentials')
        response = self.client.get('/healthz')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'status': 'unavailable'})
        self.assertNotIn(b'private', response.content)

    @patch('schoolStackPro.health.connection')
    def test_post_is_rejected_without_query(self, connection):
        cursor = connection.cursor
        self.assertEqual(self.client.post('/healthz').status_code, 405)
        cursor.assert_not_called()
