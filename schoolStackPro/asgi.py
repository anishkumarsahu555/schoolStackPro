"""
ASGI config for schoolStackPro project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/4.2/howto/deployment/asgi/
"""

import mimetypes
import os
from pathlib import Path
from urllib.parse import unquote

from django.conf import settings
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'schoolStackPro.settings')

django_application = get_asgi_application()


class StaticMediaASGIWrapper:
    def __init__(self, app):
        self.app = app
        self.static_root = Path(settings.STATIC_ROOT)
        self.media_root = Path(settings.MEDIA_ROOT)
        self.static_prefix = settings.STATIC_URL.rstrip("/") + "/"
        self.media_prefix = settings.MEDIA_URL.rstrip("/") + "/"

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        path = scope.get("path", "")
        method = scope.get("method", "GET").upper()
        if method not in {"GET", "HEAD"}:
            return await self.app(scope, receive, send)

        resolved = self._resolve_path(path)
        if not resolved:
            return await self.app(scope, receive, send)

        file_path, content_type = resolved
        headers = [
            (b"content-type", content_type.encode("utf-8")),
            (b"content-length", str(file_path.stat().st_size).encode("utf-8")),
            (b"cache-control", b"public, max-age=31536000"),
        ]
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": headers,
            }
        )
        if method == "HEAD":
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            return

        with file_path.open("rb") as file_handle:
            while True:
                chunk = file_handle.read(64 * 1024)
                if not chunk:
                    break
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    def _resolve_path(self, request_path):
        request_path = unquote(request_path or "")
        if request_path.startswith(self.static_prefix):
            return self._safe_file(self.static_root, request_path[len(self.static_prefix):])
        if request_path.startswith(self.media_prefix):
            return self._safe_file(self.media_root, request_path[len(self.media_prefix):])
        return None

    def _safe_file(self, root, relative_path):
        if not root.exists():
            return None

        normalized = Path(relative_path.lstrip("/"))
        if ".." in normalized.parts:
            return None

        target = (root / normalized).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError:
            return None

        if not target.is_file():
            return None

        content_type, _ = mimetypes.guess_type(str(target))
        return target, (content_type or "application/octet-stream")


application = StaticMediaASGIWrapper(django_application)
