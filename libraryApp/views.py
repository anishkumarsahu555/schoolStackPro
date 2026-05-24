import base64
from datetime import date
from io import BytesIO
from types import SimpleNamespace

import qrcode
from django.shortcuts import render
import json

from homeApp.models import SchoolDetail
from homeApp.utils import login_required
from libraryApp.models import LibraryMember
from libraryApp.services import (
    DEFAULT_LIBRARY_CARD_FIELDS_CONFIG,
    DEFAULT_LIBRARY_CARD_FOOTER_CONFIG,
    DEFAULT_LIBRARY_CARD_HEADER_CONFIG,
    DEFAULT_LIBRARY_CARD_STYLE_CONFIG,
    build_member_card_render_context,
    get_or_create_active_member_card_design,
    merged_config,
    normalize_library_card_fields,
)
from utils.logger import logger


def _render_library_page(request, template, page_title, page_subtitle, active):
    logger.info(f'Library page opened page={active} user={request.user.id}')
    return render(request, template, {
        'page_title_text': page_title,
        'page_subtitle_text': page_subtitle,
        'active_library_page': active,
    })


def _current_session(request):
    return request.session.get('current_session', {}) or {}


def _scope_members(request):
    current_session = _current_session(request)
    return LibraryMember.objects.select_related(
        'schoolID',
        'sessionID',
        'student',
        'student__standardID',
        'staff',
    ).filter(
        schoolID_id=current_session.get('SchoolID'),
        sessionID_id=current_session.get('Id'),
        isDeleted=False,
    )


def _qr_data_uri(value):
    qr = qrcode.QRCode(box_size=3, border=1)
    qr.add_data(value or '')
    qr.make(fit=True)
    image = qr.make_image(fill_color='black', back_color='white')
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')


def _image_url(image_field):
    if not image_field:
        return ''
    try:
        return image_field.thumbnail.url
    except Exception:
        try:
            return image_field.url
        except Exception:
            return ''


def _member_card_context(member):
    linked = member.student if member.memberType == 'student' else member.staff
    class_or_role = 'N/A'
    secondary_code = ''
    if member.student_id:
        standard = member.student.standardID
        if standard:
            class_or_role = standard.name or 'N/A'
            if standard.section:
                class_or_role = f'{class_or_role} - {standard.section}'
        secondary_code = member.student.registrationCode or ''
    elif member.staff_id:
        class_or_role = member.staff.currentPosition or member.staff.staffType or 'Staff'
        secondary_code = member.staff.employeeCode or ''
    return {
        'member': member,
        'name': member.display_name or member.memberCode,
        'member_type': member.get_memberType_display(),
        'photo_url': _image_url(getattr(linked, 'photo', None)),
        'class_or_role': class_or_role,
        'secondary_code': secondary_code,
        'qr_data_uri': _qr_data_uri(member.memberCode),
    }


def _school_fallback(school):
    return school or SimpleNamespace(
        schoolName='School Library',
        name='School Library',
        address='',
        website='',
        phoneNumber='',
        logo=None,
    )


@login_required
def dashboard(request):
    return _render_library_page(
        request,
        'libraryApp/dashboard.html',
        'Library Dashboard',
        'Track stock, circulation, reservations, fines, and exceptions',
        'dashboard',
    )


@login_required
def manage_books(request):
    return _render_library_page(request, 'libraryApp/manage_books.html', 'Book Management', 'Maintain book catalogue and metadata', 'books')


@login_required
def manage_categories(request):
    return _render_library_page(request, 'libraryApp/manage_categories.html', 'Book Categories', 'Organize books by category and subcategory', 'categories')


@login_required
def manage_authors(request):
    return _render_library_page(request, 'libraryApp/manage_authors.html', 'Authors', 'Maintain author records used by books', 'authors')


@login_required
def manage_publishers(request):
    return _render_library_page(request, 'libraryApp/manage_publishers.html', 'Publishers', 'Maintain publisher contact and catalogue records', 'publishers')


@login_required
def manage_copies(request):
    return _render_library_page(request, 'libraryApp/manage_copies.html', 'Book Copies', 'Track physical copies, accession numbers, and scan values', 'copies')


@login_required
def manage_members(request):
    return _render_library_page(request, 'libraryApp/manage_members.html', 'Library Members', 'Manage student and staff memberships', 'members')


@login_required
def member_cards(request):
    members = _scope_members(request).filter(isActive=True).order_by('memberCode')
    member_id = request.GET.get('member')
    if member_id and str(member_id).isdigit():
        members = members.filter(pk=member_id)
    member_type = request.GET.get('type')
    if member_type in {'student', 'staff'}:
        members = members.filter(memberType=member_type)

    current_session = _current_session(request)
    school = SchoolDetail.objects.filter(pk=current_session.get('SchoolID'), isDeleted=False).first()
    school = _school_fallback(school)
    design = get_or_create_active_member_card_design(current_session.get('SchoolID'), current_session.get('Id'))
    cards = [_member_card_context(member) for member in members]
    render_context = build_member_card_render_context(
        cards=cards,
        design=design,
        school=school,
        session_id=current_session.get('Id'),
    )
    logger.info(f'Library member cards opened user={request.user.id} cards={len(cards)} member={member_id or "all"}')
    context = {
        'cards': cards,
        'school': school,
        'generated_on': date.today(),
    }
    context.update(render_context)
    return render(request, 'libraryApp/member_cards.html', context)


@login_required
def member_card_design(request):
    current_session = _current_session(request)
    school_id = current_session.get('SchoolID')
    session_id = current_session.get('Id')
    school = SchoolDetail.objects.filter(pk=school_id, isDeleted=False).first()
    school = _school_fallback(school)
    design = get_or_create_active_member_card_design(school_id, session_id)
    sample_member = _scope_members(request).filter(isActive=True).order_by('memberCode').first()
    cards = [_member_card_context(sample_member)] if sample_member else []
    if cards:
        cards[0]['name'] = 'Aman Sharma'
        if cards[0]['member'].memberType == 'student':
            cards[0]['class_or_role'] = cards[0]['class_or_role'] or 'Class 8 - A'
    render_context = build_member_card_render_context(cards=cards, design=design, school=school, session_id=session_id)
    context = {
        'id_card_design': design,
        'id_card_header': merged_config(design.headerConfig, DEFAULT_LIBRARY_CARD_HEADER_CONFIG),
        'id_card_fields': normalize_library_card_fields(design.fieldsConfig),
        'id_card_style': merged_config(design.styleConfig, DEFAULT_LIBRARY_CARD_STYLE_CONFIG),
        'id_card_footer': merged_config(design.footerConfig, DEFAULT_LIBRARY_CARD_FOOTER_CONFIG),
        'header_config_json': json.dumps(merged_config(design.headerConfig, DEFAULT_LIBRARY_CARD_HEADER_CONFIG)),
        'fields_config_json': json.dumps(normalize_library_card_fields(design.fieldsConfig)),
        'style_config_json': json.dumps(merged_config(design.styleConfig, DEFAULT_LIBRARY_CARD_STYLE_CONFIG)),
        'footer_config_json': json.dumps(merged_config(design.footerConfig, DEFAULT_LIBRARY_CARD_FOOTER_CONFIG)),
        'preview_member': sample_member,
        'school': school,
    }
    context.update(render_context)
    logger.info(f'Library member card design opened user={request.user.id} design={design.id}')
    return render(request, 'libraryApp/member_card_design.html', context)


@login_required
def issue_book(request):
    return _render_library_page(request, 'libraryApp/issue_book.html', 'Issue / Return Book', 'Issue, return, renew, and calculate overdue fines', 'issue')


@login_required
def issue_history(request):
    return _render_library_page(request, 'libraryApp/issue_history.html', 'Issue History', 'Search issued, returned, renewed, lost, and damaged book history', 'issue_history')


@login_required
def return_book(request):
    return issue_book(request)


@login_required
def manage_reservations(request):
    return _render_library_page(request, 'libraryApp/manage_reservations.html', 'Book Reservations', 'Queue, fulfil, cancel, and expire reservations', 'reservations')


@login_required
def manage_fines(request):
    return _render_library_page(request, 'libraryApp/manage_fines.html', 'Library Fines', 'Track overdue, lost, damaged, and manual fines', 'fines')


@login_required
def settings(request):
    return _render_library_page(request, 'libraryApp/settings.html', 'Library Settings', 'Configure borrowing, fines, reservations, and defaults', 'settings')


@login_required
def reports(request):
    return _render_library_page(request, 'libraryApp/reports.html', 'Library Reports', 'Review circulation, overdue, stock, and fine reports', 'reports')
