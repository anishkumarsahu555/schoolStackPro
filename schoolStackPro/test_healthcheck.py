"""Verify the lightweight container probe without a database or network."""
import io
from unittest.mock import patch
from urllib.error import HTTPError

from django.test import SimpleTestCase
from schoolStackPro.healthcheck import main


class ProbeTests(SimpleTestCase):
    @patch.dict('os.environ', {'ALLOWED_HOSTS': ' .example.com, localhost', 'APP_PORT': '8123'})
    @patch('schoolStackPro.healthcheck.urllib.request.build_opener')
    def test_custom_host_port_and_valid_json(self, build):
        response = io.BytesIO(b'{ "status" : "ok" }')
        response.status = 200
        build.return_value.open.return_value = response
        main()
        request = build.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url, 'http://127.0.0.1:8123/healthz')
        self.assertEqual(request.get_header('Host'), 'example.com')
        self.assertEqual(build.call_args.args[0].proxies, {})

    @patch('schoolStackPro.healthcheck.urllib.request.build_opener')
    def test_rejects_wrong_success_body(self, build):
        response = io.BytesIO(b'{"status":"unavailable"}')
        response.status = 200
        build.return_value.open.return_value = response
        with self.assertRaises(SystemExit):
            main()

    @patch('schoolStackPro.healthcheck.urllib.request.build_opener')
    def test_database_failure_does_not_pass(self, build):
        build.return_value.open.side_effect = HTTPError('/healthz', 503, 'Unavailable', {}, None)
        with self.assertRaises(HTTPError):
            main()
