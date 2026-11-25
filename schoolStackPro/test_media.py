from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.http import Http404
from django.test import AsyncRequestFactory, RequestFactory, SimpleTestCase
from django.urls import resolve

from schoolStackPro.media import uploaded_media


class UploadTests(SimpleTestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / 'uploads'
        self.enterContext(self.settings(MEDIA_ROOT=self.root))
        self.name = default_storage.save('nested/photo.png', ContentFile(b'image'))
        self.url = default_storage.url(self.name)
        self.assertIs(resolve(self.url).func, uploaded_media)

    def request(self, method='get', authenticated=True):
        request = getattr(RequestFactory(), method)(self.url)
        request.user = SimpleNamespace(is_authenticated=authenticated)
        return request

    def test_read_after_upload(self):
        with closing(uploaded_media(self.request(), self.name)) as response:
            self.assertEqual(b''.join(response.streaming_content), b'image')
            self.assertIn('inline', response['Content-Disposition'])
            self.assertIn('no-store', response['Cache-Control'])
        with closing(uploaded_media(self.request('head'), self.name)) as response:
            self.assertEqual(response['Content-Length'], '5')
            self.assertEqual(b''.join(response.streaming_content), b'')

    def test_session_methods_and_download(self):
        self.assertEqual(uploaded_media(self.request(authenticated=False), self.name).status_code, 403)
        self.assertEqual(uploaded_media(self.request('post'), self.name).status_code, 405)
        name = default_storage.save('document.html', ContentFile(b'document'))
        with closing(uploaded_media(self.request(), name)) as response:
            self.assertIn('attachment', response['Content-Disposition'])
            self.assertEqual(response['X-Content-Type-Options'], 'nosniff')

    def test_hidden_files_and_paths_outside_uploads_are_unavailable(self):
        (self.root / '.hidden').write_bytes(b'fixture')
        outside = self.root.parent / 'outside.txt'
        outside.write_bytes(b'fixture')
        (self.root / 'link').symlink_to(outside)
        for name in ('.hidden', 'link', '../outside.txt', 'nested', 'missing'):
            with self.subTest(name=name), self.assertRaises(Http404):
                uploaded_media(self.request(), name)

    async def test_asgi_stream(self):
        request = AsyncRequestFactory().get(self.url)
        request.user = SimpleNamespace(is_authenticated=True)
        response = uploaded_media(request, self.name)
        try:
            self.assertTrue(response.is_async)
            self.assertEqual(b''.join([chunk async for chunk in response]), b'image')
        finally:
            response.close()
