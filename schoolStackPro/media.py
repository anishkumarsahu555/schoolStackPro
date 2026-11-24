"""Serve mutable local uploads through Django session authentication."""
import mimetypes
from pathlib import Path

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.handlers.asgi import ASGIRequest
from django.http import FileResponse, Http404, HttpResponseForbidden
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe


async def _chunks(stream):
    read = sync_to_async(stream.read)
    while chunk := await read(64 * 1024):
        yield chunk


@never_cache
@require_safe
def uploaded_media(request, filename):
    if not request.user.is_authenticated:
        return HttpResponseForbidden()

    relative = Path(filename)
    if relative.is_absolute() or any(part.startswith('.') for part in relative.parts):
        raise Http404
    try:
        root = Path(settings.MEDIA_ROOT).resolve()
        target = (root / relative).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise Http404
        stream = target.open('rb')
    except (OSError, ValueError, RuntimeError):
        raise Http404 from None

    content_type = mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
    inline = content_type in {'image/png', 'image/jpeg', 'image/gif', 'image/webp',
                              'image/avif', 'image/bmp', 'image/tiff', 'image/x-icon',
                              'image/vnd.microsoft.icon'}
    response = FileResponse(stream, content_type=content_type, as_attachment=not inline)
    if request.method == 'HEAD':
        response.streaming_content = ()
    elif isinstance(request, ASGIRequest):
        # Django otherwise buffers a synchronous FileResponse iterator under ASGI.
        response.streaming_content = _chunks(stream)
    response['Content-Security-Policy'] = "sandbox; default-src 'none'"
    response['X-Content-Type-Options'] = 'nosniff'
    return response
