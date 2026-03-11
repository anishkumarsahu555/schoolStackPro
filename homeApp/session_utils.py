import calendar
import re
from datetime import date


def _safe_logo_url(school):
    if not school or not getattr(school, 'logo', None):
        return None
    try:
        return school.logo.url
    except Exception:
        return None


def derive_session_year_label(session_obj):
    if not session_obj:
        return ''
    if getattr(session_obj, 'sessionYear', None):
        return session_obj.sessionYear

    start_date = getattr(session_obj, 'startDate', None)
    end_date = getattr(session_obj, 'endDate', None)
    if start_date and end_date:
        if start_date.year == end_date.year:
            return str(start_date.year)
        return f"{start_date.year}-{str(end_date.year)[-2:]}"
    if start_date:
        return str(start_date.year)
    return ''


def session_range_label(session_obj):
    if not session_obj:
        return ''
    start_date = getattr(session_obj, 'startDate', None)
    end_date = getattr(session_obj, 'endDate', None)
    if start_date and end_date:
        return f"{start_date.strftime('%b %Y')} - {end_date.strftime('%b %Y')}"
    if start_date:
        return f"From {start_date.strftime('%b %Y')}"
    if end_date:
        return f"Until {end_date.strftime('%b %Y')}"
    return ''


def build_current_session_payload(session_obj):
    school = getattr(session_obj, 'schoolID', None)
    return {
        'currentSessionYear': derive_session_year_label(session_obj),
        'Id': session_obj.pk,
        'SchoolID': getattr(session_obj, 'schoolID_id', None),
        'SchoolName': getattr(school, 'schoolName', None) if school else None,
        'SchoolLogo': _safe_logo_url(school),
        'SessionStartDate': session_obj.startDate.isoformat() if getattr(session_obj, 'startDate', None) else None,
        'SessionEndDate': session_obj.endDate.isoformat() if getattr(session_obj, 'endDate', None) else None,
        'SessionRangeLabel': session_range_label(session_obj),
    }


def build_session_list_item(session_obj):
    return {
        'currentSessionYear': derive_session_year_label(session_obj),
        'Id': session_obj.pk,
        'SessionStartDate': session_obj.startDate.isoformat() if getattr(session_obj, 'startDate', None) else None,
        'SessionEndDate': session_obj.endDate.isoformat() if getattr(session_obj, 'endDate', None) else None,
        'SessionRangeLabel': session_range_label(session_obj),
    }


def _infer_start_year(session_year_value):
    if not session_year_value:
        return None
    text_value = str(session_year_value)
    match = re.search(r'(19|20)\d{2}', text_value)
    if not match:
        return None
    try:
        return int(match.group(0))
    except ValueError:
        return None


def get_session_month_sequence(session_obj, max_months=None):
    start_date = getattr(session_obj, 'startDate', None) if session_obj else None
    end_date = getattr(session_obj, 'endDate', None) if session_obj else None

    if start_date and end_date and start_date <= end_date:
        months = []
        cursor = date(start_date.year, start_date.month, 1)
        last_cursor = date(end_date.year, end_date.month, 1)
        while cursor <= last_cursor and (max_months is None or len(months) < max_months):
            months.append((
                calendar.month_name[cursor.month],
                cursor.year,
                cursor.month,
                cursor,
                date(cursor.year, cursor.month, _month_end_day(cursor.year, cursor.month)),
            ))
            if cursor.month == 12:
                cursor = date(cursor.year + 1, 1, 1)
            else:
                cursor = date(cursor.year, cursor.month + 1, 1)
        if months:
            return months

    start_year = _infer_start_year(getattr(session_obj, 'sessionYear', None) if session_obj else None)
    if not start_year:
        start_year = date.today().year

    fallback = []
    for month_no in range(1, 13):
        fallback.append((
            calendar.month_name[month_no],
            start_year,
            month_no,
            date(start_year, month_no, 1),
            date(start_year, month_no, _month_end_day(start_year, month_no)),
        ))
    return fallback


def _month_end_day(year, month):
    return calendar.monthrange(year, month)[1]
