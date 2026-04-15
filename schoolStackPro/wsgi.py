"""
WSGI config for schoolStackPro project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/4.2/howto/deployment/wsgi/
"""

import os
from pathlib import Path

from django.conf import settings
from django.core.wsgi import get_wsgi_application
from whitenoise import WhiteNoise

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'schoolStackPro.settings')

django_application = get_wsgi_application()

application = WhiteNoise(django_application)

static_root = Path(settings.STATIC_ROOT)
media_root = Path(settings.MEDIA_ROOT)

if static_root.exists():
    application.add_files(str(static_root), prefix=settings.STATIC_URL.lstrip('/'))

if media_root.exists():
    application.add_files(str(media_root), prefix=settings.MEDIA_URL.lstrip('/'))
