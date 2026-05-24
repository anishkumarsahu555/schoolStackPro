from datetime import date, datetime

from homeApp.models import SchoolSession
from libraryApp.models import LibraryMemberCardDesign


DEFAULT_LIBRARY_CARD_HEADER_CONFIG = {
    'layout': 'masthead',
    'showLogo': True,
    'showSchoolName': True,
    'showAddress': True,
    'showPhone': True,
    'showWebsite': True,
    'title': 'Library Card',
    'subtitle': 'Library Membership',
    'addressText': '',
    'phoneNumber': '',
    'websiteUrl': '',
    'schoolNameFontSize': 15,
    'logoSizeMm': 4.6,
    'addressFontSize': 8.8,
    'contactFontSize': 8.3,
    'titleFontSize': 9,
    'subtitleFontSize': 7,
}

DEFAULT_LIBRARY_CARD_FIELDS_CONFIG = [
    {'key': 'memberCode', 'label': 'Member ID', 'visible': True},
    {'key': 'name', 'label': 'Name', 'visible': True},
    {'key': 'memberType', 'label': 'Type', 'visible': True},
    {'key': 'classOrRole', 'label': 'Class/Role', 'visible': True},
    {'key': 'secondaryCode', 'label': 'Reg./Emp. Code', 'visible': True},
    {'key': 'maxBooks', 'label': 'Max Books', 'visible': True},
    {'key': 'joinDate', 'label': 'Join Date', 'visible': True},
    {'key': 'fineLimit', 'label': 'Fine Limit', 'visible': False},
]

DEFAULT_LIBRARY_CARD_STYLE_CONFIG = {
    'primaryColor': '#2452a3',
    'headerColor': '#2452a3',
    'headerTextColor': '#ffffff',
    'cardBackgroundColor': '#f3f5f8',
    'textColor': '#122030',
    'labelColor': '#324f75',
    'fontFamily': 'Arial, sans-serif',
    'photoShape': 'rounded',
    'showQr': True,
    'showBarcode': True,
}

DEFAULT_LIBRARY_CARD_FOOTER_CONFIG = {
    'showValidity': True,
    'validityMode': 'session_end',
    'validityText': '',
    'validTill': '',
    'showSignature': True,
    'showSignatureImage': True,
    'signatureLabel': 'Librarian Signature',
}


def merged_config(config, defaults):
    merged = dict(defaults)
    if isinstance(config, dict):
        merged.update(config)
    return merged


def normalize_library_card_fields(fields_config):
    saved_map = {}
    if isinstance(fields_config, list):
        for item in fields_config:
            if isinstance(item, dict) and item.get('key'):
                saved_map[item['key']] = item

    normalized = []
    for default in DEFAULT_LIBRARY_CARD_FIELDS_CONFIG:
        saved = saved_map.get(default['key'], {})
        normalized.append({
            'key': default['key'],
            'label': saved.get('label') or default['label'],
            'visible': bool(saved.get('visible', default['visible'])),
        })
    return normalized


def get_or_create_active_member_card_design(school_id, session_id):
    design = LibraryMemberCardDesign.objects.filter(
        schoolID_id=school_id,
        sessionID_id=session_id,
        isActive=True,
        isDeleted=False,
    ).order_by('-lastUpdatedOn').first()
    if design:
        return design
    return LibraryMemberCardDesign.objects.create(
        schoolID_id=school_id,
        sessionID_id=session_id,
        name='Default Library Card Design',
        headerConfig=DEFAULT_LIBRARY_CARD_HEADER_CONFIG,
        fieldsConfig=DEFAULT_LIBRARY_CARD_FIELDS_CONFIG,
        styleConfig=DEFAULT_LIBRARY_CARD_STYLE_CONFIG,
        footerConfig=DEFAULT_LIBRARY_CARD_FOOTER_CONFIG,
    )


def get_school_name(school):
    if not school:
        return 'School Library'
    return school.schoolName or school.name or 'School Library'


def format_date(value, fmt='%d-%m-%Y'):
    if not value:
        return 'N/A'
    if isinstance(value, date):
        return value.strftime(fmt)
    return str(value)


def parse_iso_date(value):
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return datetime.strptime(str(value), '%Y-%m-%d').date()
    except ValueError:
        return None


def resolve_member_field(card, key):
    member = card['member']
    if key == 'memberCode':
        return member.memberCode or 'N/A'
    if key == 'name':
        return card.get('name') or 'N/A'
    if key == 'memberType':
        return card.get('member_type') or 'N/A'
    if key == 'classOrRole':
        return card.get('class_or_role') or 'N/A'
    if key == 'secondaryCode':
        return card.get('secondary_code') or 'N/A'
    if key == 'maxBooks':
        return str(member.maxBooksAllowed)
    if key == 'joinDate':
        return format_date(member.joinDate)
    if key == 'fineLimit':
        return str(member.fineLimit)
    return 'N/A'


def resolve_library_validity_label(footer_config, session_id=None):
    if not footer_config.get('showValidity', True) or footer_config.get('validityMode') == 'hidden':
        return ''
    mode = footer_config.get('validityMode') or 'session_end'
    if mode == 'custom_text':
        return footer_config.get('validityText') or ''
    if mode == 'custom_date':
        parsed = parse_iso_date(footer_config.get('validTill'))
        return format_date(parsed) if parsed else ''
    session = SchoolSession.objects.filter(pk=session_id, isDeleted=False).first() if session_id else None
    for attr in ('endDate', 'toDate', 'sessionEndDate'):
        value = getattr(session, attr, None)
        if value:
            return format_date(value)
    return footer_config.get('validityText') or ''


def resolve_library_session_label(session_id=None):
    session = SchoolSession.objects.filter(pk=session_id, isDeleted=False).first() if session_id else None
    if not session:
        return ''
    return session.sessionYear or ''


def build_member_card_render_context(*, cards, design, school, session_id=None):
    header = merged_config(design.headerConfig, DEFAULT_LIBRARY_CARD_HEADER_CONFIG)
    fields = normalize_library_card_fields(design.fieldsConfig)
    style = merged_config(design.styleConfig, DEFAULT_LIBRARY_CARD_STYLE_CONFIG)
    footer = merged_config(design.footerConfig, DEFAULT_LIBRARY_CARD_FOOTER_CONFIG)
    return {
        'id_card_design': design,
        'id_card_header': header,
        'id_card_fields': fields,
        'id_card_style': style,
        'id_card_footer': footer,
        'school_name': get_school_name(school),
        'session_label': resolve_library_session_label(session_id),
        'valid_till_label': resolve_library_validity_label(footer, session_id),
        'cards': [
            {
                **card,
                'field_rows': [
                    {
                        **field,
                        'value': resolve_member_field(card, field['key']),
                    }
                    for field in fields
                ],
            }
            for card in cards
        ],
    }
