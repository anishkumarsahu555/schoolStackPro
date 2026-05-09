from datetime import date, datetime

from homeApp.models import SchoolSession
from managementApp.models import StudentIdCardDesign, StudentIdCardRecord


DEFAULT_HEADER_CONFIG = {
    'layout': 'masthead',
    'showLogo': True,
    'showSchoolName': True,
    'showAddress': True,
    'showPhone': True,
    'showWebsite': True,
    'title': '',
    'subtitle': '',
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

DEFAULT_FIELDS_CONFIG = [
    {'key': 'registrationCode', 'label': 'Regd. No.', 'visible': True},
    {'key': 'name', 'label': 'Name', 'visible': True},
    {'key': 'class', 'label': 'Class', 'visible': True},
    {'key': 'roll', 'label': 'Roll No.', 'visible': True},
    {'key': 'fatherName', 'label': "Father's Name", 'visible': True},
    {'key': 'motherName', 'label': "Mother's Name", 'visible': True},
    {'key': 'dob', 'label': 'D.O.B', 'visible': True},
    {'key': 'bloodGroup', 'label': 'Blood Group', 'visible': False},
    {'key': 'address', 'label': 'Address', 'visible': True},
    {'key': 'phone', 'label': 'Contact No.', 'visible': True},
]

DEFAULT_STYLE_CONFIG = {
    'primaryColor': '#2452a3',
    'headerColor': '#ffffff',
    'headerTextColor': '#122030',
    'cardBackgroundColor': '#f3f5f8',
    'textColor': '#122030',
    'labelColor': '#324f75',
    'fontFamily': 'Arial, sans-serif',
    'photoShape': 'rounded',
}

DEFAULT_FOOTER_CONFIG = {
    'showValidity': True,
    'validityMode': 'session_end',
    'validityText': '',
    'validTill': '',
    'showSignature': True,
    'showSignatureImage': True,
    'signatureLabel': 'Principal Signature',
}


def merged_config(config, defaults):
    merged = dict(defaults)
    if isinstance(config, dict):
        merged.update(config)
    return merged


def normalize_fields_config(fields_config):
    saved_map = {}
    if isinstance(fields_config, list):
        for item in fields_config:
            if isinstance(item, dict) and item.get('key'):
                saved_map[item['key']] = item

    normalized = []
    for default in DEFAULT_FIELDS_CONFIG:
        saved = saved_map.get(default['key'], {})
        normalized.append({
            'key': default['key'],
            'label': saved.get('label') or default['label'],
            'visible': bool(saved.get('visible', default['visible'])),
        })
    return normalized


def get_or_create_active_id_card_design(school_id, session_id):
    design = StudentIdCardDesign.objects.filter(
        schoolID_id=school_id,
        sessionID_id=session_id,
        isActive=True,
        isDeleted=False,
    ).order_by('-lastUpdatedOn').first()

    if design:
        return design

    return StudentIdCardDesign.objects.create(
        schoolID_id=school_id,
        sessionID_id=session_id,
        name='Default ID Card Design',
        headerConfig=DEFAULT_HEADER_CONFIG,
        fieldsConfig=DEFAULT_FIELDS_CONFIG,
        styleConfig=DEFAULT_STYLE_CONFIG,
        footerConfig=DEFAULT_FOOTER_CONFIG,
    )


def get_school_name(school):
    if not school:
        return 'School Name'
    return school.schoolName or school.name or 'School Name'


def get_class_label(student):
    standard = getattr(student, 'standardID', None)
    if not standard:
        return 'N/A'
    label = standard.name or 'N/A'
    if standard.section:
        label = f'{label} - {standard.section}'
    return label


def first_value(*values):
    for value in values:
        if value:
            return value
    return 'N/A'


def format_date(value, fmt='%d-%m-%Y'):
    if not value:
        return 'N/A'
    if isinstance(value, date):
        return value.strftime(fmt)
    return str(value)


def resolve_student_field(student, key):
    parent = getattr(student, 'parentID', None)
    if key == 'registrationCode':
        return student.registrationCode or 'N/A'
    if key == 'name':
        return student.name or 'N/A'
    if key == 'class':
        return get_class_label(student)
    if key == 'roll':
        return student.roll or 'N/A'
    if key == 'fatherName':
        return getattr(parent, 'fatherName', None) or 'N/A'
    if key == 'motherName':
        return getattr(parent, 'motherName', None) or 'N/A'
    if key == 'dob':
        return format_date(student.dob)
    if key == 'bloodGroup':
        return student.bloodGroup or 'N/A'
    if key == 'address':
        return first_value(
            student.presentAddress,
            student.permanentAddress,
            getattr(parent, 'fatherAddress', None),
            getattr(parent, 'motherAddress', None),
        )
    if key == 'phone':
        return first_value(
            student.phoneNumber,
            getattr(parent, 'phoneNumber', None),
            getattr(parent, 'fatherPhone', None),
            getattr(parent, 'motherPhone', None),
        )
    return 'N/A'


def parse_iso_date(value):
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return datetime.strptime(str(value), '%Y-%m-%d').date()
    except ValueError:
        return None


def get_latest_student_valid_till(student, session_id):
    record = StudentIdCardRecord.objects.filter(
        studentID=student,
        sessionID_id=session_id,
        isDeleted=False,
        validTill__isnull=False,
    ).order_by('-lastUpdatedOn').first()
    return record.validTill if record else None


def resolve_valid_till_label(design, session, student=None):
    footer = merged_config(getattr(design, 'footerConfig', {}), DEFAULT_FOOTER_CONFIG)
    mode = footer.get('validityMode') or 'session_end'

    if mode == 'hidden' or not footer.get('showValidity', True):
        return ''

    if mode == 'custom_text':
        return footer.get('validityText') or ''

    valid_till = None
    if student and getattr(student, 'sessionID_id', None):
        valid_till = get_latest_student_valid_till(student, student.sessionID_id)

    if not valid_till and mode == 'custom_date':
        valid_till = parse_iso_date(footer.get('validTill'))

    if not valid_till and mode == 'session_end' and session:
        valid_till = session.endDate

    if valid_till:
        return f"Upto {valid_till.strftime('%B %Y')}"

    return 'Upto December 2026'


def build_id_card_context(student, school=None, session=None, design=None):
    school = school or student.schoolID
    session = session or student.sessionID
    if not session and getattr(student, 'sessionID_id', None):
        session = SchoolSession.objects.filter(pk=student.sessionID_id).first()

    if not design:
        design = get_or_create_active_id_card_design(
            school.id if school else student.schoolID_id,
            session.id if session else student.sessionID_id,
        )

    header = merged_config(design.headerConfig, DEFAULT_HEADER_CONFIG)
    style = merged_config(design.styleConfig, DEFAULT_STYLE_CONFIG)
    footer = merged_config(design.footerConfig, DEFAULT_FOOTER_CONFIG)
    fields_config = normalize_fields_config(design.fieldsConfig)
    field_rows = [
        {
            'key': field['key'],
            'label': field['label'],
            'value': resolve_student_field(student, field['key']),
            'visible': field.get('visible'),
        }
        for field in fields_config
    ]

    return {
        'instance': student,
        'school': school,
        'school_name': get_school_name(school),
        'id_card_design': design,
        'id_card_header': header,
        'id_card_style': style,
        'id_card_footer': footer,
        'id_card_fields': fields_config,
        'field_rows': field_rows,
        'valid_till_label': resolve_valid_till_label(design, session, student),
    }
