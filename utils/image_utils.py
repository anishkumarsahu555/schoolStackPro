import os
from io import BytesIO

from django.core.files.uploadedfile import InMemoryUploadedFile
from django.templatetags.static import static
from django.utils.html import escape
from PIL import Image, ImageOps, UnidentifiedImageError


def safe_image_url(image_field, fallback_path='images/default_avatar.svg'):
    if not image_field:
        return static(fallback_path)

    thumbnail = getattr(image_field, 'thumbnail', None)
    if thumbnail:
        try:
            return thumbnail.url
        except Exception:
            pass

    try:
        return image_field.url
    except Exception:
        return static(fallback_path)


def avatar_image_html(image_field, css_class='ui avatar image'):
    src = escape(safe_image_url(image_field))
    classes = escape(css_class)
    return (
        f'<img class="{classes}" src="{src}" alt="Avatar" '
        f'loading="lazy" decoding="async" referrerpolicy="no-referrer">'
    )


def optimize_uploaded_image(uploaded_file, max_width=1280, max_height=1280, jpeg_quality=82):
    if not uploaded_file:
        return uploaded_file

    try:
        if hasattr(uploaded_file, 'seek'):
            uploaded_file.seek(0)
        image = Image.open(uploaded_file)
        image = ImageOps.exif_transpose(image)
    except (UnidentifiedImageError, OSError, AttributeError):
        return uploaded_file

    original_format = (image.format or '').upper()
    has_alpha = image.mode in ('RGBA', 'LA') or (
        image.mode == 'P' and 'transparency' in image.info
    )

    if image.width > max_width or image.height > max_height:
        image.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)

    output = BytesIO()
    base_name = os.path.splitext(uploaded_file.name or 'image')[0]

    if has_alpha and original_format in {'PNG', 'WEBP'}:
        if original_format == 'WEBP':
            image.save(output, format='WEBP', quality=85, method=6)
            ext = 'webp'
            content_type = 'image/webp'
        else:
            image.save(output, format='PNG', optimize=True)
            ext = 'png'
            content_type = 'image/png'
    else:
        if image.mode != 'RGB':
            image = image.convert('RGB')
        image.save(
            output,
            format='JPEG',
            quality=jpeg_quality,
            optimize=True,
            progressive=True,
        )
        ext = 'jpg'
        content_type = 'image/jpeg'

    output.seek(0)
    return InMemoryUploadedFile(
        file=output,
        field_name='image',
        name=f'{base_name}.{ext}',
        content_type=content_type,
        size=output.getbuffer().nbytes,
        charset=None,
    )
