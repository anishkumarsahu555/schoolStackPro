from html import escape
from html.parser import HTMLParser
from urllib.parse import urlparse


ALLOWED_HTML_TAGS = {
    'a', 'b', 'blockquote', 'br', 'code', 'div', 'em', 'h1', 'h2', 'h3', 'h4',
    'h5', 'h6', 'hr', 'i', 'li', 'ol', 'p', 'pre', 's', 'span', 'strong',
    'sub', 'sup', 'table', 'tbody', 'td', 'th', 'thead', 'tr', 'u', 'ul',
}

ALLOWED_STYLE_PROPERTIES = {
    'background-color', 'color', 'font-size', 'font-weight', 'font-style',
    'text-align', 'text-decoration', 'vertical-align',
}

ALLOWED_LINK_SCHEMES = {'', 'http', 'https', 'mailto', 'tel'}


def _safe_url(value):
    parsed = urlparse((value or '').strip())
    return parsed.scheme.lower() in ALLOWED_LINK_SCHEMES


def _sanitize_style(value):
    clean_rules = []
    for rule in (value or '').split(';'):
        if ':' not in rule:
            continue
        prop, raw_value = rule.split(':', 1)
        prop = prop.strip().lower()
        raw_value = raw_value.strip()
        lowered = raw_value.lower()
        if prop not in ALLOWED_STYLE_PROPERTIES:
            continue
        if any(token in lowered for token in ('expression', 'javascript:', 'url(')):
            continue
        clean_rules.append(f'{prop}: {raw_value}')
    return '; '.join(clean_rules)


class _SubjectNoteHTMLSanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in {'script', 'style', 'iframe', 'object', 'embed'}:
            self._skip_depth += 1
            return
        if self._skip_depth or tag not in ALLOWED_HTML_TAGS:
            return

        clean_attrs = []
        for name, value in attrs:
            name = (name or '').lower()
            value = value or ''
            if name.startswith('on'):
                continue
            if name == 'href' and tag == 'a' and _safe_url(value):
                clean_attrs.append(('href', value.strip()))
                continue
            if name == 'target' and tag == 'a' and value in {'_blank', '_self'}:
                clean_attrs.append(('target', value))
                if value == '_blank':
                    clean_attrs.append(('rel', 'noopener noreferrer'))
                continue
            if name == 'style':
                clean_style = _sanitize_style(value)
                if clean_style:
                    clean_attrs.append(('style', clean_style))

        attr_text = ''.join(f' {name}="{escape(value, quote=True)}"' for name, value in clean_attrs)
        self.parts.append(f'<{tag}{attr_text}>')

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {'script', 'style', 'iframe', 'object', 'embed'}:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth or tag not in ALLOWED_HTML_TAGS or tag in {'br', 'hr'}:
            return
        self.parts.append(f'</{tag}>')

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_data(self, data):
        if not self._skip_depth:
            self.parts.append(escape(data))

    def handle_entityref(self, name):
        if not self._skip_depth:
            self.parts.append(f'&{name};')

    def handle_charref(self, name):
        if not self._skip_depth:
            self.parts.append(f'&#{name};')


def sanitize_subject_note_html(value):
    sanitizer = _SubjectNoteHTMLSanitizer()
    sanitizer.feed(value or '')
    sanitizer.close()
    return ''.join(sanitizer.parts).strip()
