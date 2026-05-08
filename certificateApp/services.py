from datetime import date, datetime
import json
import os
from pathlib import Path
import re
import secrets
from decimal import Decimal
from string import Template
from types import SimpleNamespace
from urllib.parse import unquote, urlparse

from django.conf import settings
from django.contrib.staticfiles import finders
from django.db import transaction
from django.db.models import Q
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from playwright.sync_api import sync_playwright

from homeApp.models import SchoolDetail, SchoolSession
from managementApp.models import Parent, Student, TeacherDetail

from .models import CertificateDesign, CertificateIssue, CertificateSequence, CertificateType
from .qr import qr_svg_data_uri


SYSTEM_CERTIFICATE_TYPES = [
    {
        'slug': 'bonafide-certificate',
        'name': 'Bonafide Certificate',
        'recipientCategory': 'student',
        'description': 'Confirms that the student is currently enrolled in the school.',
        'defaultTitle': 'Bonafide Certificate',
        'defaultSubtitle': 'Official Student Verification',
        'defaultBodyTemplate': (
            'This is to certify that the above-named student is enrolled at ${school_name} and is studying in ${class_name} '
            'during the academic session ${session_year}. This certificate is issued upon request for official and lawful use.'
        ),
        'defaultFooterText': 'Issued on ${issue_date} at the request of the student or guardian for official submission.',
    },
    {
        'slug': 'character-certificate',
        'name': 'Character Certificate',
        'recipientCategory': 'student',
        'description': 'States the conduct and behavior of the student.',
        'defaultTitle': 'Character Certificate',
        'defaultSubtitle': 'Conduct and Character Record',
        'defaultBodyTemplate': (
            'This is to certify that the above-named student has maintained good conduct, disciplined behavior, and satisfactory character '
            'at ${school_name} during the session ${session_year}. We wish ${student_pronoun_object} continued success in all future endeavors.'
        ),
        'defaultFooterText': 'Principal / Authorized Signatory',
    },
    {
        'slug': 'transfer-certificate',
        'name': 'Transfer Certificate',
        'recipientCategory': 'student',
        'description': 'Confirms that the student has been relieved from the school.',
        'defaultTitle': 'Transfer Certificate',
        'defaultSubtitle': 'Official School Record',
        'defaultBodyTemplate': (
            'As per the school record, admission no. ${admission_no} studied in ${class_name} and is hereby granted transfer from ${school_name}. '
            'The student stands relieved from the rolls of the institution with effect from ${issue_date}.'
        ),
        'defaultFooterText': 'Issue of this certificate remains subject to clearance of dues and verification of school records.',
    },
    {
        'slug': 'fee-paid-certificate',
        'name': 'Fee Paid Certificate',
        'recipientCategory': 'student',
        'description': 'Acknowledges fee payment status for a student.',
        'defaultTitle': 'Fee Paid Certificate',
        'defaultSubtitle': 'School Fee Payment Confirmation',
        'defaultBodyTemplate': (
            'This is to certify that the required school fee for the session ${session_year} has been paid as per the records maintained by ${school_name}.'
        ),
        'defaultFooterText': 'For transaction-wise detail and receipt reference, kindly consult the school finance ledger.',
    },
    {
        'slug': 'participation-certificate',
        'name': 'Participation Certificate',
        'recipientCategory': 'student',
        'description': 'Recognizes participation in an event or school activity.',
        'defaultTitle': 'Certificate of Participation',
        'defaultSubtitle': 'Presented With Appreciation',
        'defaultBodyTemplate': (
            'This certificate is awarded in recognition of active participation in school activities during session ${session_year} at ${school_name}. '
            'The institution records its appreciation for the enthusiasm, sincerity, and involvement shown throughout the event.'
        ),
        'defaultFooterText': 'With appreciation and best wishes.',
    },
    {
        'slug': 'experience-certificate',
        'name': 'Experience Certificate',
        'recipientCategory': 'teacher',
        'description': 'Certifies service history for a teacher or staff member.',
        'defaultTitle': 'Experience Certificate',
        'defaultSubtitle': 'Service Verification Record',
        'defaultBodyTemplate': (
            'This is to certify that the above-named staff member served at ${school_name} as ${designation} during session '
            '${session_year}. ${teacher_pronoun_subject_cap} discharged assigned duties with sincerity, dedication, and professional conduct.'
        ),
        'defaultFooterText': 'Issued upon request for official purposes.',
    },
]


SYSTEM_DESIGNS = [
    {
        'slug': 'classic-formal',
        'name': 'Classic Formal',
        'templateKey': 'classic_formal',
        'pageSize': 'A4',
        'orientation': 'portrait',
        'titleAlignment': 'center',
        'bodyAlignment': 'center',
        'borderStyle': 'double',
        'fontFamily': 'Georgia',
        'accentColor': '#1d4ed8',
        'textColor': '#1f2937',
        'backgroundColor': '#ffffff',
        'customHeaderText': 'Academic Records',
    },
    {
        'slug': 'modern-clean',
        'name': 'Modern Clean',
        'templateKey': 'modern_clean',
        'pageSize': 'A4',
        'orientation': 'portrait',
        'titleAlignment': 'left',
        'bodyAlignment': 'left',
        'borderStyle': 'single',
        'fontFamily': 'Trebuchet MS',
        'accentColor': '#0f766e',
        'textColor': '#1f2937',
        'backgroundColor': '#f8fafc',
        'customHeaderText': 'Official Communication',
    },
    {
        'slug': 'ceremonial-gold',
        'name': 'Ceremonial Gold',
        'templateKey': 'ceremonial_gold',
        'pageSize': 'A4',
        'orientation': 'landscape',
        'titleAlignment': 'center',
        'bodyAlignment': 'center',
        'borderStyle': 'ornate',
        'fontFamily': 'Palatino',
        'accentColor': '#b45309',
        'textColor': '#422006',
        'backgroundColor': '#fffaf0',
        'customHeaderText': 'Certificate Collection',
    },
    {
        'slug': 'academic-seal',
        'name': 'Academic Seal',
        'templateKey': 'academic_seal',
        'pageSize': 'A4',
        'orientation': 'portrait',
        'titleAlignment': 'center',
        'bodyAlignment': 'center',
        'borderStyle': 'single',
        'fontFamily': 'Georgia',
        'accentColor': '#7c2d12',
        'textColor': '#3f2a1d',
        'backgroundColor': '#fffdf8',
        'customHeaderText': 'Institutional Certificate',
    },
    {
        'slug': 'heritage-script',
        'name': 'Heritage Script',
        'templateKey': 'heritage_script',
        'pageSize': 'A4',
        'orientation': 'landscape',
        'titleAlignment': 'center',
        'bodyAlignment': 'center',
        'borderStyle': 'ribbon',
        'fontFamily': 'Palatino',
        'accentColor': '#7c3f00',
        'textColor': '#4b2e1f',
        'backgroundColor': '#fffaf4',
        'customHeaderText': 'Commemorative Distinction',
    },
    {
        'slug': 'minimal-duotone',
        'name': 'Minimal Duotone',
        'templateKey': 'minimal_duotone',
        'pageSize': 'A4',
        'orientation': 'portrait',
        'titleAlignment': 'left',
        'bodyAlignment': 'left',
        'borderStyle': 'none',
        'fontFamily': 'Arial',
        'accentColor': '#0f172a',
        'textColor': '#111827',
        'backgroundColor': '#f8fafc',
        'customHeaderText': 'School Certificate',
    },
    {
        'slug': 'emerald-ledger',
        'name': 'Emerald Ledger',
        'templateKey': 'ledger_grid',
        'pageSize': 'A4',
        'orientation': 'portrait',
        'titleAlignment': 'left',
        'bodyAlignment': 'left',
        'borderStyle': 'single',
        'fontFamily': 'Trebuchet MS',
        'accentColor': '#047857',
        'textColor': '#1f2937',
        'backgroundColor': '#f3fbf7',
        'customHeaderText': 'Academic Ledger',
        'customFooterText': 'Structured for finance, transfer, and record-heavy certificates.',
    },
    {
        'slug': 'midnight-laurel',
        'name': 'Midnight Laurel',
        'templateKey': 'laurel_frame',
        'pageSize': 'A4',
        'orientation': 'portrait',
        'titleAlignment': 'center',
        'bodyAlignment': 'center',
        'borderStyle': 'double',
        'fontFamily': 'Georgia',
        'accentColor': '#1e3a8a',
        'textColor': '#172554',
        'backgroundColor': '#f8fbff',
        'customHeaderText': 'Honor Roll Citation',
        'customFooterText': 'Reserved for distinction, rank, and excellence certificates.',
    },
    {
        'slug': 'sunrise-merit',
        'name': 'Sunrise Merit',
        'templateKey': 'ribbon_banner',
        'pageSize': 'A4',
        'orientation': 'landscape',
        'titleAlignment': 'center',
        'bodyAlignment': 'center',
        'borderStyle': 'ribbon',
        'fontFamily': 'Palatino',
        'accentColor': '#c2410c',
        'textColor': '#7c2d12',
        'backgroundColor': '#fff7ed',
        'customHeaderText': 'Merit Recognition',
        'customFooterText': 'Suitable for merit, cultural, and school event certificates.',
    },
    {
        'slug': 'slate-editorial',
        'name': 'Slate Editorial',
        'templateKey': 'editorial_grid',
        'pageSize': 'A4',
        'orientation': 'portrait',
        'titleAlignment': 'left',
        'bodyAlignment': 'left',
        'borderStyle': 'none',
        'fontFamily': 'Arial',
        'accentColor': '#334155',
        'textColor': '#0f172a',
        'backgroundColor': '#f8fafc',
        'customHeaderText': 'Institutional Brief',
        'customFooterText': 'Editorial-style spacing for concise official certificates.',
    },
    {
        'slug': 'ivory-ribbon',
        'name': 'Ivory Ribbon',
        'templateKey': 'ribbon_banner',
        'pageSize': 'LETTER',
        'orientation': 'landscape',
        'titleAlignment': 'center',
        'bodyAlignment': 'center',
        'borderStyle': 'ribbon',
        'fontFamily': 'Palatino',
        'accentColor': '#9a3412',
        'textColor': '#431407',
        'backgroundColor': '#fffbeb',
        'customHeaderText': 'Commendation Series',
        'customFooterText': 'Suitable for achievement and school function certificates.',
    },
    {
        'slug': 'cobalt-panel',
        'name': 'Cobalt Panel',
        'templateKey': 'split_panel',
        'pageSize': 'A4',
        'orientation': 'landscape',
        'titleAlignment': 'left',
        'bodyAlignment': 'left',
        'borderStyle': 'single',
        'fontFamily': 'Trebuchet MS',
        'accentColor': '#1d4ed8',
        'textColor': '#1e293b',
        'backgroundColor': '#f5f9ff',
        'customHeaderText': 'Professional Issue Desk',
        'customFooterText': 'Strong horizontal layout for staff, experience, and service certificates.',
    },
    {
        'slug': 'rose-archive',
        'name': 'Rose Archive',
        'templateKey': 'crest_band',
        'pageSize': 'A5',
        'orientation': 'portrait',
        'titleAlignment': 'center',
        'bodyAlignment': 'center',
        'borderStyle': 'single',
        'fontFamily': 'Georgia',
        'accentColor': '#be185d',
        'textColor': '#831843',
        'backgroundColor': '#fff7fb',
        'customHeaderText': 'Archive Copy',
        'customFooterText': 'Compact premium layout for small-format issue certificates.',
    },
    {
        'slug': 'olive-scholar',
        'name': 'Olive Scholar',
        'templateKey': 'classic_formal',
        'pageSize': 'A4',
        'orientation': 'portrait',
        'titleAlignment': 'center',
        'bodyAlignment': 'center',
        'borderStyle': 'double',
        'fontFamily': 'Times New Roman',
        'accentColor': '#4d7c0f',
        'textColor': '#365314',
        'backgroundColor': '#f7fee7',
        'customHeaderText': 'Scholastic Register',
        'customFooterText': 'Suitable for bonafide and academic standing certificates.',
    },
    {
        'slug': 'pearl-citation',
        'name': 'Pearl Citation',
        'templateKey': 'royal_arc',
        'pageSize': 'A4',
        'orientation': 'landscape',
        'titleAlignment': 'center',
        'bodyAlignment': 'center',
        'borderStyle': 'ornate',
        'fontFamily': 'Palatino',
        'accentColor': '#7c3aed',
        'textColor': '#4c1d95',
        'backgroundColor': '#faf5ff',
        'customHeaderText': 'Citation Gallery',
        'customFooterText': 'Suitable for formal recognition and appreciation certificates.',
    },
    {
        'slug': 'graphite-column',
        'name': 'Graphite Column',
        'templateKey': 'editorial_grid',
        'pageSize': 'LETTER',
        'orientation': 'portrait',
        'titleAlignment': 'left',
        'bodyAlignment': 'left',
        'borderStyle': 'none',
        'fontFamily': 'Arial',
        'accentColor': '#111827',
        'textColor': '#111827',
        'backgroundColor': '#fcfcfd',
        'customHeaderText': 'Formal Registry',
        'customFooterText': 'Suitable for formal institutional and administrative certificates.',
    },
    {
        'slug': 'teal-horizon',
        'name': 'Teal Horizon',
        'templateKey': 'ribbon_banner',
        'pageSize': 'LETTER',
        'orientation': 'landscape',
        'titleAlignment': 'center',
        'bodyAlignment': 'center',
        'borderStyle': 'ribbon',
        'fontFamily': 'Palatino',
        'accentColor': '#0f766e',
        'textColor': '#134e4a',
        'backgroundColor': '#f0fdfa',
        'customHeaderText': 'Celebration Horizon',
        'customFooterText': 'Suitable for participation and school activity certificates.',
    },
    {
        'slug': 'amber-broadcast',
        'name': 'Amber Broadcast',
        'templateKey': 'split_panel',
        'pageSize': 'A5',
        'orientation': 'landscape',
        'titleAlignment': 'left',
        'bodyAlignment': 'left',
        'borderStyle': 'single',
        'fontFamily': 'Trebuchet MS',
        'accentColor': '#d97706',
        'textColor': '#78350f',
        'backgroundColor': '#fffbeb',
        'customHeaderText': 'Quick Recognition',
        'customFooterText': 'Suitable for club, event, and short-format certificates.',
    },
    {
        'slug': 'sports-meet-stripe',
        'name': 'Sports Meet Stripe',
        'templateKey': 'split_panel',
        'pageSize': 'LETTER',
        'orientation': 'landscape',
        'titleAlignment': 'left',
        'bodyAlignment': 'left',
        'borderStyle': 'single',
        'fontFamily': 'Trebuchet MS',
        'accentColor': '#0f766e',
        'textColor': '#134e4a',
        'backgroundColor': '#f0fdfa',
        'customHeaderText': 'Annual Sports Meet',
        'customFooterText': 'Suitable for sports meet, award, and team certificates.',
    },
    {
        'slug': 'board-merit-frame',
        'name': 'Board Merit Frame',
        'templateKey': 'laurel_frame',
        'pageSize': 'A4',
        'orientation': 'portrait',
        'titleAlignment': 'center',
        'bodyAlignment': 'center',
        'borderStyle': 'double',
        'fontFamily': 'Georgia',
        'accentColor': '#7c2d12',
        'textColor': '#431407',
        'backgroundColor': '#fffbf5',
        'customHeaderText': 'Board Merit Citation',
        'customFooterText': 'Suitable for board merit and examination distinction certificates.',
    },
    {
        'slug': 'festival-banner',
        'name': 'Festival Banner',
        'templateKey': 'ribbon_banner',
        'pageSize': 'A4',
        'orientation': 'landscape',
        'titleAlignment': 'center',
        'bodyAlignment': 'center',
        'borderStyle': 'ribbon',
        'fontFamily': 'Palatino',
        'accentColor': '#9333ea',
        'textColor': '#581c87',
        'backgroundColor': '#faf5ff',
        'customHeaderText': 'Cultural Festival Honors',
        'customFooterText': 'Suitable for cultural, music, and festival certificates.',
    },
    {
        'slug': 'staff-service-ledger',
        'name': 'Staff Service Ledger',
        'templateKey': 'ledger_grid',
        'pageSize': 'A4',
        'orientation': 'portrait',
        'titleAlignment': 'left',
        'bodyAlignment': 'left',
        'borderStyle': 'single',
        'fontFamily': 'Arial',
        'accentColor': '#475569',
        'textColor': '#1e293b',
        'backgroundColor': '#f8fafc',
        'customHeaderText': 'Service Verification Desk',
        'customFooterText': 'Suitable for staff service and employment record certificates.',
    },
    {
        'slug': 'science-fair-citation',
        'name': 'Science Fair Citation',
        'templateKey': 'royal_arc',
        'pageSize': 'LETTER',
        'orientation': 'landscape',
        'titleAlignment': 'center',
        'bodyAlignment': 'center',
        'borderStyle': 'ornate',
        'fontFamily': 'Palatino',
        'accentColor': '#2563eb',
        'textColor': '#1e3a8a',
        'backgroundColor': '#eff6ff',
        'customHeaderText': 'Innovation Showcase',
        'customFooterText': 'Suitable for fairs, exhibitions, and innovation award certificates.',
    },
    {
        'slug': 'attendance-column',
        'name': 'Attendance Column',
        'templateKey': 'editorial_grid',
        'pageSize': 'A5',
        'orientation': 'portrait',
        'titleAlignment': 'left',
        'bodyAlignment': 'left',
        'borderStyle': 'none',
        'fontFamily': 'Arial',
        'accentColor': '#0f172a',
        'textColor': '#1f2937',
        'backgroundColor': '#ffffff',
        'customHeaderText': 'Attendance & Record Slip',
        'customFooterText': 'Suitable for attendance and routine school record certificates.',
    },
    {
        'slug': 'house-championship-plaque',
        'name': 'House Championship Plaque',
        'templateKey': 'award_plaque',
        'pageSize': 'A4',
        'orientation': 'landscape',
        'titleAlignment': 'center',
        'bodyAlignment': 'center',
        'borderStyle': 'double',
        'fontFamily': 'Times New Roman',
        'accentColor': '#b91c1c',
        'textColor': '#7f1d1d',
        'backgroundColor': '#fef2f2',
        'customHeaderText': 'House Championship Honors',
        'customFooterText': 'Suitable for championship and inter-house award certificates.',
    },
    {
        'slug': 'principal-honors-note',
        'name': 'Principal Honors Note',
        'templateKey': 'crest_band',
        'pageSize': 'LETTER',
        'orientation': 'portrait',
        'titleAlignment': 'left',
        'bodyAlignment': 'left',
        'borderStyle': 'single',
        'fontFamily': 'Trebuchet MS',
        'accentColor': '#1d4ed8',
        'textColor': '#0f172a',
        'backgroundColor': '#f8fbff',
        'customHeaderText': 'Principal Honors Desk',
        'customFooterText': 'Suitable for principal appreciation and honors certificates.',
    },
    {
        'slug': 'community-service-scroll',
        'name': 'Community Service Scroll',
        'templateKey': 'ribbon_banner',
        'pageSize': 'A4',
        'orientation': 'portrait',
        'titleAlignment': 'center',
        'bodyAlignment': 'center',
        'borderStyle': 'ribbon',
        'fontFamily': 'Palatino',
        'accentColor': '#0f766e',
        'textColor': '#115e59',
        'backgroundColor': '#f0fdfa',
        'customHeaderText': 'Service & Outreach Commendation',
        'customFooterText': 'Suitable for outreach, service, and volunteer recognition certificates.',
    },
    {
        'slug': 'hand-fill-form',
        'name': 'Hand-Fill Form',
        'templateKey': 'hand_fill_form',
        'pageSize': 'A4',
        'orientation': 'portrait',
        'titleAlignment': 'center',
        'bodyAlignment': 'left',
        'borderStyle': 'single',
        'fontFamily': 'Georgia',
        'accentColor': '#334155',
        'textColor': '#1f2937',
        'backgroundColor': '#ffffff',
        'customHeaderText': 'Write-In Certificate Sheet',
        'customFooterText': 'Use this format for manual handwriting and event-day issue.',
    },
    {
        'slug': 'prize-day-form',
        'name': 'Prize Day Form',
        'templateKey': 'prize_day_form',
        'pageSize': 'A4',
        'orientation': 'landscape',
        'titleAlignment': 'center',
        'bodyAlignment': 'left',
        'borderStyle': 'double',
        'fontFamily': 'Georgia',
        'accentColor': '#7c2d12',
        'textColor': '#3f2a1d',
        'backgroundColor': '#fffdf8',
        'customHeaderText': 'Prize Distribution Write-In',
        'customFooterText': 'Prepared for manual filling during school events and ceremonies.',
    },
]


FONT_STACKS = {
    'georgia': 'Georgia, "Times New Roman", Times, serif',
    'times new roman': '"Times New Roman", Times, serif',
    'palatino': '"Palatino Linotype", "Book Antiqua", Palatino, Georgia, serif',
    'trebuchet ms': '"Trebuchet MS", "Lucida Sans Unicode", "Lucida Grande", "Lucida Sans", Arial, sans-serif',
    'arial': 'Arial, Helvetica, sans-serif',
    'verdana': 'Verdana, Geneva, sans-serif',
    'courier new': '"Courier New", Courier, monospace',
}


OVERLAY_FIELD_PRESETS = {
    'title': {'label': 'Certificate Title', 'template': '${title}', 'x': 50, 'y': 22, 'width': 72, 'fontSize': 24, 'align': 'center', 'fontWeight': '700', 'lineHeight': 1.12},
    'subtitle': {'label': 'Certificate Subtitle', 'template': '${subtitle}', 'x': 50, 'y': 30, 'width': 72, 'fontSize': 11, 'align': 'center', 'fontWeight': '500', 'lineHeight': 1.24},
    'student_name': {'label': 'Student Name', 'template': '${student_name}', 'x': 50, 'y': 44, 'width': 74, 'fontSize': 30, 'align': 'center', 'fontWeight': '700', 'lineHeight': 1.08},
    'teacher_name': {'label': 'Teacher Name', 'template': '${teacher_name}', 'x': 50, 'y': 44, 'width': 74, 'fontSize': 28, 'align': 'center', 'fontWeight': '700', 'lineHeight': 1.08},
    'recipient_subtitle': {'label': 'Recipient Subtitle', 'template': '${recipient_subtitle}', 'x': 50, 'y': 53, 'width': 68, 'fontSize': 12, 'align': 'center', 'fontWeight': '500', 'lineHeight': 1.22},
    'body': {'label': 'Body Copy', 'template': '${rendered_body}', 'x': 50, 'y': 63, 'width': 76, 'fontSize': 13, 'align': 'center', 'fontWeight': '400', 'lineHeight': 1.5},
    'issue_date': {'label': 'Issue Date', 'template': '${issue_date}', 'x': 82, 'y': 88, 'width': 18, 'fontSize': 11, 'align': 'right', 'fontWeight': '600', 'lineHeight': 1.15},
    'certificate_no': {'label': 'Certificate Number', 'template': '${certificate_no}', 'x': 12, 'y': 88, 'width': 28, 'fontSize': 10, 'align': 'left', 'fontWeight': '600', 'lineHeight': 1.15},
    'footer': {'label': 'Footer Note', 'template': '${rendered_footer}', 'x': 18, 'y': 92, 'width': 44, 'fontSize': 10, 'align': 'left', 'fontWeight': '400', 'lineHeight': 1.32},
    'school_name': {'label': 'School Name', 'template': '${school_name}', 'x': 50, 'y': 11, 'width': 74, 'fontSize': 18, 'align': 'center', 'fontWeight': '700', 'lineHeight': 1.1},
    'event_name': {'label': 'Event Name', 'template': 'Annual School Event', 'x': 50, 'y': 72, 'width': 60, 'fontSize': 14, 'align': 'center', 'fontWeight': '600', 'lineHeight': 1.24},
}


def _user_label(user_obj):
    if not user_obj:
        return None
    full_name = f'{user_obj.first_name} {user_obj.last_name}'.strip()
    return full_name or user_obj.username


def default_overlay_schema():
    return [
        _build_overlay_field('school_name'),
        _build_overlay_field('title'),
        _build_overlay_field('subtitle'),
        _build_overlay_field('student_name'),
        _build_overlay_field('recipient_subtitle'),
        _build_overlay_field('body'),
        _build_overlay_field('certificate_no'),
        _build_overlay_field('issue_date'),
    ]


def _build_overlay_field(key, item_id=None):
    preset = OVERLAY_FIELD_PRESETS.get(key, {})
    return {
        'id': item_id or key,
        'key': key,
        'label': preset.get('label', pretty_overlay_label(key)),
        'template': preset.get('template', '${' + key + '}'),
        'x': preset.get('x', 50),
        'y': preset.get('y', 50),
        'width': preset.get('width', 40),
        'fontSize': preset.get('fontSize', 14),
        'color': preset.get('color', '#1f2937'),
        'align': preset.get('align', 'center'),
        'fontWeight': preset.get('fontWeight', '600'),
        'letterSpacing': preset.get('letterSpacing', 0),
        'lineHeight': preset.get('lineHeight', 1.35),
    }


def pretty_overlay_label(value):
    return str(value or '').replace('_', ' ').title()


def resolve_font_stack(raw_font_family):
    normalized = str(raw_font_family or '').strip()
    if not normalized:
        return FONT_STACKS['georgia']
    return FONT_STACKS.get(normalized.lower(), normalized)


def normalize_overlay_schema(raw_schema):
    if not raw_schema:
        return []

    if isinstance(raw_schema, str):
        try:
            raw_schema = json.loads(raw_schema)
        except json.JSONDecodeError:
            return []

    normalized = []
    for index, item in enumerate(raw_schema):
        if not isinstance(item, dict):
            continue
        key = str(item.get('key') or item.get('source') or f'field_{index + 1}').strip() or f'field_{index + 1}'
        normalized.append({
            'id': str(item.get('id') or f'overlay_{index + 1}'),
            'key': key,
            'label': str(item.get('label') or pretty_overlay_label(key)).strip() or pretty_overlay_label(key),
            'template': str(item.get('template') or item.get('text') or '${' + key + '}'),
            'x': max(0, min(100, float(item.get('x', 50) or 50))),
            'y': max(0, min(100, float(item.get('y', 50) or 50))),
            'width': max(8, min(100, float(item.get('width', 40) or 40))),
            'fontSize': max(8, min(72, float(item.get('fontSize', 14) or 14))),
            'color': str(item.get('color') or '#1f2937'),
            'align': str(item.get('align') or 'center'),
            'fontWeight': str(item.get('fontWeight') or '600'),
            'letterSpacing': float(item.get('letterSpacing', 0) or 0),
            'lineHeight': max(0.8, min(3.0, float(item.get('lineHeight', 1.35) or 1.35))),
        })
    return normalized


def resolve_overlay_items(*, design, scope_data, title, subtitle, recipient_name, recipient_subtitle, rendered_body, rendered_footer):
    if not design or design.designMode != 'image_overlay':
        return []

    schema = normalize_overlay_schema(design.overlaySchema)
    if not schema:
        schema = default_overlay_schema()

    payload = {
        **scope_data,
        'title': title,
        'subtitle': subtitle,
        'recipient_name': recipient_name,
        'recipient_subtitle': recipient_subtitle,
        'rendered_body': rendered_body,
        'rendered_footer': rendered_footer,
    }
    payload.setdefault('student_name', recipient_name)
    payload.setdefault('teacher_name', recipient_name)
    payload.setdefault('recipient_subtitle', recipient_subtitle)

    resolved = []
    for item in schema:
        text = Template(str(item.get('template') or '')).safe_substitute(payload).strip()
        resolved.append({**item, 'text': text})
    return resolved


def _mm_to_pt(value):
    return float(value or 0) * 72.0 / 25.4


def _mm_to_px(value, dpi=96):
    return max(1, int(round(float(value or 0) * float(dpi) / 25.4)))


def _pdf_escape(value):
    text = str(value or '').replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
    return text.encode('latin-1', 'replace').decode('latin-1')


def _hex_to_rgb(value, fallback='#1d4ed8'):
    raw = str(value or fallback).strip()
    if not raw.startswith('#') or len(raw) != 7:
        raw = fallback
    return tuple(int(raw[i:i + 2], 16) / 255.0 for i in (1, 3, 5))


def _page_dimensions_pt(design):
    size = getattr(design, 'pageSize', 'A4') if design else 'A4'
    orientation = getattr(design, 'orientation', 'portrait') if design else 'portrait'
    if size == 'A5':
        width_mm, height_mm = 148, 210
    elif size == 'LETTER':
        width_mm, height_mm = 216, 279
    elif size == 'CUSTOM' and design and design.pageWidthMm and design.pageHeightMm:
        width_mm, height_mm = float(design.pageWidthMm), float(design.pageHeightMm)
    else:
        width_mm, height_mm = 210, 297
    if orientation == 'landscape':
        width_mm, height_mm = height_mm, width_mm
    return _mm_to_pt(width_mm), _mm_to_pt(height_mm)


def _wrap_pdf_text(text, max_width, font_size, weight='regular'):
    words = str(text or '').replace('\r', '').split()
    if not words:
        return ['']
    width_factor = 0.56 if weight == 'bold' else 0.5
    max_chars = max(12, int(max_width / (font_size * width_factor)))
    lines = []
    current = ''
    for word in words:
        trial = word if not current else current + ' ' + word
        if len(trial) <= max_chars:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _text_x_for_align(text, x_left, width, font_size, align='left'):
    if align != 'center':
        return x_left
    estimated_width = len(text) * font_size * 0.52
    return x_left + max(0, (width - estimated_width) / 2.0)


def render_certificate_document(context, *, request=None):
    return render_to_string('certificateApp/partials/certificate_document.html', context, request=request)


def render_issue_document(issue, *, request=None, render_mode='preview'):
    context = build_issue_context(issue, request=request)
    context['render_mode'] = render_mode
    return render_certificate_document(context, request=request)


def build_issue_pdf(issue, *, request=None):
    context = build_issue_context(issue, request=request)
    context['render_mode'] = 'print'
    context['is_pdf_export'] = True
    html = render_to_string('certificateApp/issue_browser_pdf.html', context)
    html = _replace_pdf_asset_urls(html)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page_width_px = _mm_to_px(context.get('page_width_mm', 210))
        page_height_px = _mm_to_px(context.get('page_height_mm', 297))
        browser_context = browser.new_context(
            viewport={'width': page_width_px, 'height': page_height_px},
            screen={'width': page_width_px, 'height': page_height_px},
            device_scale_factor=2,
        )
        page = browser_context.new_page()
        page.set_content(html, wait_until='load')
        page.wait_for_load_state('networkidle')
        page.emulate_media(media='print')
        pdf_bytes = page.pdf(
            print_background=True,
            prefer_css_page_size=True,
            margin={'top': '0', 'right': '0', 'bottom': '0', 'left': '0'},
        )
        browser_context.close()
        browser.close()
        return pdf_bytes


def _replace_pdf_asset_urls(html):
    def replace_attr(match):
        prefix, quote, raw_url, suffix = match.groups()
        asset_path = _asset_url_to_file_uri(raw_url)
        return f'{prefix}{quote}{asset_path}{quote}{suffix}'

    def replace_css(match):
        quote, raw_url = match.groups()
        asset_path = _asset_url_to_file_uri(raw_url)
        return f'url({quote}{asset_path}{quote})'

    html = re.sub(r'((?:src|href)=)(["\'])([^"\']+)(["\'])', replace_attr, html)
    html = re.sub(r'url\((["\']?)([^)"\']+)\1\)', replace_css, html)
    return html


def _asset_url_to_file_uri(uri):
    if not uri or uri.startswith(('data:', 'file:', 'http:', 'https:')):
        return uri

    parsed = urlparse(uri)
    path = unquote(parsed.path or uri)

    if os.path.isabs(path) and os.path.exists(path):
        return Path(path).resolve().as_uri()

    media_url = str(getattr(settings, 'MEDIA_URL', '') or '')
    static_url = str(getattr(settings, 'STATIC_URL', '') or '')

    if media_url and path.startswith(media_url):
        media_path = Path(settings.MEDIA_ROOT) / path[len(media_url):].lstrip('/')
        if media_path.exists():
            return media_path.resolve().as_uri()

    if static_url and path.startswith(static_url):
        static_path = finders.find(path[len(static_url):].lstrip('/'))
        if static_path:
            return Path(static_path).resolve().as_uri()

    return uri


def _unique_design_slug(*, certificate_type, school_id, base_name):
    base_slug = slugify(base_name) or 'certificate-design'
    candidate = base_slug
    counter = 2
    while CertificateDesign.objects.filter(
        certificateTypeID=certificate_type,
        schoolID_id=school_id,
        slug=candidate,
    ).exists():
        candidate = f'{base_slug}-{counter}'
        counter += 1
    return candidate


def get_request_scope(request):
    current_session = request.session.get('current_session', {})
    session_id = current_session.get('Id')
    school_id = current_session.get('SchoolID')
    if not school_id and session_id:
        school_id = SchoolSession.objects.filter(pk=session_id, isDeleted=False).values_list('schoolID_id', flat=True).first()
    school = SchoolDetail.objects.filter(pk=school_id, isDeleted=False).first() if school_id else None
    session_obj = SchoolSession.objects.filter(pk=session_id, isDeleted=False).first() if session_id else None
    return school, session_obj


def ensure_system_certificate_defaults(*, school_id=None, user_obj=None):
    changed = False
    for type_data in SYSTEM_CERTIFICATE_TYPES:
        cert_type, created = CertificateType.objects.get_or_create(
            schoolID_id=school_id,
            slug=type_data['slug'],
            defaults={
                **type_data,
                'isSystem': True,
                'lastEditedBy': _user_label(user_obj),
                'updatedByUserID': user_obj,
            },
        )
        if created:
            changed = True
        else:
            changed_fields = []
            for field_name, field_value in type_data.items():
                if getattr(cert_type, field_name) != field_value:
                    setattr(cert_type, field_name, field_value)
                    changed_fields.append(field_name)
            if changed_fields:
                cert_type.lastEditedBy = _user_label(user_obj)
                cert_type.updatedByUserID = user_obj
                changed_fields.extend(['lastEditedBy', 'updatedByUserID', 'lastUpdatedOn'])
                cert_type.save(update_fields=changed_fields)
                changed = True
        for design_data in SYSTEM_DESIGNS:
            design_obj, created = CertificateDesign.objects.get_or_create(
                certificateTypeID=cert_type,
                schoolID_id=school_id,
                slug=design_data['slug'],
                defaults={
                    **{k: v for k, v in design_data.items() if k != 'slug'},
                    'slug': design_data['slug'],
                    'isSystem': True,
                    'lastEditedBy': _user_label(user_obj),
                    'updatedByUserID': user_obj,
                },
            )
            if not created:
                changed_fields = []
                for field_name, field_value in design_data.items():
                    if field_name == 'slug':
                        continue
                    if getattr(design_obj, field_name) != field_value:
                        setattr(design_obj, field_name, field_value)
                        changed_fields.append(field_name)
                if changed_fields:
                    design_obj.lastEditedBy = _user_label(user_obj)
                    design_obj.updatedByUserID = user_obj
                    changed_fields.extend(['lastEditedBy', 'updatedByUserID', 'lastUpdatedOn'])
                    design_obj.save(update_fields=changed_fields)
    return changed


def get_certificate_types(*, school_id=None):
    return CertificateType.objects.filter(
        Q(schoolID_id=school_id) | Q(schoolID__isnull=True),
        isDeleted=False,
        isActive=True,
    ).order_by('name')


def get_certificate_designs(*, certificate_type, school_id=None, include_inactive=False):
    queryset = CertificateDesign.objects.filter(
        Q(schoolID_id=school_id) | Q(schoolID__isnull=True),
        certificateTypeID=certificate_type,
        isDeleted=False,
    )
    if not include_inactive:
        queryset = queryset.filter(isActive=True)
    return queryset.order_by('-isCustom', 'status', 'name')


def get_recipient_options(*, recipient_category, school_id=None, session_id=None):
    if recipient_category == 'student':
        queryset = Student.objects.select_related('standardID').filter(
            isDeleted=False,
            schoolID_id=school_id,
            sessionID_id=session_id,
        ).order_by('name')
        rows = []
        for student in queryset:
            class_label = student.standardID.name if student.standardID_id and student.standardID else 'Class N/A'
            if student.standardID and student.standardID.section:
                class_label = f'{class_label} - {student.standardID.section}'
            rows.append({
                'id': student.id,
                'label': f'{student.name} ({class_label})',
                'previewData': {
                    'recipient_name': student.name or 'Student',
                    'student_name': student.name or 'Student',
                    'recipient_subtitle': class_label,
                    'class_name': class_label,
                    'father_name': student.parentID.fatherName if student.parentID_id and student.parentID else 'N/A',
                    'mother_name': student.parentID.motherName if student.parentID_id and student.parentID else 'N/A',
                    'roll_no': student.roll or 'N/A',
                    'admission_no': student.registrationCode or 'N/A',
                },
            })
        return rows

    if recipient_category in {'teacher', 'staff'}:
        queryset = TeacherDetail.objects.filter(
            isDeleted=False,
            schoolID_id=school_id,
            sessionID_id=session_id,
        ).order_by('name')
        rows = []
        for teacher in queryset:
            label = teacher.name or 'Unnamed Teacher'
            if teacher.staffType:
                label = f'{label} ({teacher.staffType})'
            rows.append({
                'id': teacher.id,
                'label': label,
                'previewData': {
                    'recipient_name': teacher.name or 'Teacher',
                    'teacher_name': teacher.name or 'Teacher',
                    'recipient_subtitle': teacher.currentPosition or teacher.staffType or 'Faculty Member',
                    'designation': teacher.currentPosition or teacher.staffType or 'Faculty Member',
                    'employee_code': teacher.employeeCode or 'N/A',
                },
            })
        return rows

    if recipient_category == 'parent':
        queryset = Parent.objects.filter(
            isDeleted=False,
            schoolID_id=school_id,
            sessionID_id=session_id,
        ).order_by('fatherName')
        return [{
            'id': parent.id,
            'label': parent.fatherName or 'Parent',
            'previewData': {
                'recipient_name': parent.fatherName or 'Parent',
                'parent_name': parent.fatherName or 'Parent',
                'father_name': parent.fatherName or '',
                'mother_name': parent.motherName or '',
                'recipient_subtitle': parent.phoneNumber or '',
            },
        } for parent in queryset]

    if recipient_category == 'school':
        return [{
            'id': school_id,
            'label': 'Current School',
            'previewData': {
                'recipient_name': 'School',
                'recipient_subtitle': '',
            },
        }] if school_id else []

    return []


def _pronouns_from_gender(value):
    gender = (value or '').strip().lower()
    if gender in {'male', 'm'}:
        return {'subject': 'he', 'object': 'him', 'subject_cap': 'He'}
    if gender in {'female', 'f'}:
        return {'subject': 'she', 'object': 'her', 'subject_cap': 'She'}
    return {'subject': 'they', 'object': 'them', 'subject_cap': 'They'}


def _absolute_uri(request, path):
    if request:
        return request.build_absolute_uri(path)
    base_url = str(getattr(settings, 'PUBLIC_BASE_URL', '') or '').rstrip('/')
    return f'{base_url}{path}' if base_url else path


def build_verification_url(issue, *, request=None):
    if not issue.verificationToken:
        return ''
    path = reverse('certificateApp:verify_certificate', kwargs={'token': issue.verificationToken})
    return _absolute_uri(request, path)


def build_issue_context(issue, *, request=None):
    school = issue.schoolID
    session_obj = issue.sessionID
    design = issue.certificateDesignID
    cert_type = issue.certificateTypeID

    data = {
        'school_name': school.schoolName if school else 'School Name',
        'school_address': school.address if school else '',
        'school_city': school.city if school and school.city else '',
        'school_state': school.state if school and school.state else '',
        'school_phone': school.phoneNumber if school and school.phoneNumber else '',
        'school_email': school.email if school and school.email else '',
        'session_year': session_obj.sessionYear if session_obj else '',
        'issue_date': issue.issueDate.strftime('%d-%m-%Y') if issue.issueDate else '',
        'certificate_no': issue.certificateNumber,
    }

    recipient_name = 'School'
    subtitle_name = ''
    if issue.recipientCategory == 'student' and issue.studentID:
        student = issue.studentID
        pronouns = _pronouns_from_gender(student.gender)
        class_label = student.standardID.name if student.standardID_id and student.standardID else 'N/A'
        if student.standardID and student.standardID.section:
            class_label = f'{class_label} - {student.standardID.section}'
        recipient_name = student.name or 'Student'
        subtitle_name = class_label
        data.update({
            'student_name': recipient_name,
            'class_name': class_label,
            'father_name': student.parentID.fatherName if student.parentID_id and student.parentID else 'N/A',
            'mother_name': student.parentID.motherName if student.parentID_id and student.parentID else 'N/A',
            'roll_no': student.roll or 'N/A',
            'admission_no': student.registrationCode or 'N/A',
            'student_pronoun_subject': pronouns['subject'],
            'student_pronoun_object': pronouns['object'],
            'student_pronoun_subject_cap': pronouns['subject_cap'],
        })

    elif issue.recipientCategory in {'teacher', 'staff'} and issue.teacherID:
        teacher = issue.teacherID
        pronouns = _pronouns_from_gender(teacher.gender)
        recipient_name = teacher.name or 'Teacher'
        subtitle_name = teacher.currentPosition or teacher.staffType or 'Faculty Member'
        data.update({
            'teacher_name': recipient_name,
            'designation': subtitle_name,
            'employee_code': teacher.employeeCode or 'N/A',
            'teacher_pronoun_subject': pronouns['subject'],
            'teacher_pronoun_object': pronouns['object'],
            'teacher_pronoun_subject_cap': pronouns['subject_cap'],
        })

    elif issue.recipientCategory == 'parent' and issue.parentID:
        parent = issue.parentID
        recipient_name = parent.fatherName or 'Parent'
        subtitle_name = parent.phoneNumber or ''
        data.update({
            'parent_name': recipient_name,
            'father_name': parent.fatherName or '',
            'mother_name': parent.motherName or '',
        })

    body_template = issue.customBodyText or cert_type.defaultBodyTemplate or ''
    footer_template = issue.customFooterText or cert_type.defaultFooterText or ''
    title = issue.customTitle or cert_type.defaultTitle or cert_type.name
    subtitle = issue.customSubtitle or cert_type.defaultSubtitle or ''

    rendered_body = Template(body_template).safe_substitute(data)
    rendered_footer = Template(footer_template).safe_substitute(data)

    page_size = design.pageSize if design else 'A4'
    page_width_mm = Decimal('210')
    page_height_mm = Decimal('297')
    if design and design.pageSize == 'A5':
        page_width_mm = Decimal('148')
        page_height_mm = Decimal('210')
    elif design and design.pageSize == 'LETTER':
        page_width_mm = Decimal('216')
        page_height_mm = Decimal('279')
    elif design and design.pageSize == 'CUSTOM' and design.pageWidthMm and design.pageHeightMm:
        page_width_mm = design.pageWidthMm
        page_height_mm = design.pageHeightMm
    if design and design.pageSize == 'CUSTOM' and design.pageWidthMm and design.pageHeightMm:
        page_size = f'{design.pageWidthMm}mm {design.pageHeightMm}mm'

    orientation = design.orientation if design else 'portrait'
    display_page_width_mm = page_width_mm if orientation != 'landscape' else page_height_mm
    display_page_height_mm = page_height_mm if orientation != 'landscape' else page_width_mm
    margin_top = design.marginTopMm if design else Decimal('12')
    margin_right = design.marginRightMm if design else Decimal('12')
    margin_bottom = design.marginBottomMm if design else Decimal('12')
    margin_left = design.marginLeftMm if design else Decimal('12')
    content_width_mm = max(Decimal('20'), display_page_width_mm - margin_left - margin_right)
    content_height_mm = max(Decimal('20'), display_page_height_mm - margin_top - margin_bottom)

    context = {
        'issue': issue,
        'certificate_type': cert_type,
        'design': design,
        'render_mode': 'preview',
        'design_font_stack': resolve_font_stack(design.fontFamily if design else 'Georgia'),
        'title': title,
        'subtitle': subtitle,
        'recipient_name': recipient_name,
        'recipient_subtitle': subtitle_name,
        'rendered_body': rendered_body,
        'rendered_footer': rendered_footer,
        'page_size_css': page_size,
        'margin_top': margin_top,
        'margin_right': margin_right,
        'margin_bottom': margin_bottom,
        'margin_left': margin_left,
        'page_width_mm': display_page_width_mm,
        'page_height_mm': display_page_height_mm,
        'content_width_mm': content_width_mm,
        'content_height_mm': content_height_mm,
        'scope_data': data,
        'verification_url': build_verification_url(issue, request=request),
        'verification_qr_data_uri': '',
    }
    if context['verification_url']:
        context['verification_qr_data_uri'] = qr_svg_data_uri(context['verification_url'])
    context['resolved_overlay_items'] = resolve_overlay_items(
        design=design,
        scope_data=data,
        title=title,
        subtitle=subtitle,
        recipient_name=recipient_name,
        recipient_subtitle=subtitle_name,
        rendered_body=rendered_body,
        rendered_footer=rendered_footer,
    )
    context['is_image_overlay'] = bool(design and design.designMode == 'image_overlay')
    return context


def build_generator_preview_context(*, request, certificate_type, design, recipient_id=None, issue_date=None, custom_title='', custom_subtitle='', custom_body_text='', custom_footer_text=''):
    school, session_obj = get_request_scope(request)

    data = {
        'school_name': school.schoolName if school else 'School Name',
        'school_address': school.address if school else '',
        'school_city': school.city if school and school.city else '',
        'school_state': school.state if school and school.state else '',
        'school_phone': school.phoneNumber if school and school.phoneNumber else '',
        'school_email': school.email if school and school.email else '',
        'session_year': session_obj.sessionYear if session_obj else '',
        'issue_date': issue_date.strftime('%d-%m-%Y') if issue_date else '',
        'certificate_no': 'Generated on final issue',
    }

    recipient_name = 'School'
    subtitle_name = ''
    recipient_category = certificate_type.recipientCategory
    sample_student_name = 'Aarav Sharma'
    sample_teacher_name = 'Priya Mehta'
    sample_class_name = 'Class X - A'
    sample_designation = 'Senior Faculty'

    if recipient_category == 'student' and recipient_id:
        student = Student.objects.select_related('standardID', 'parentID').filter(pk=recipient_id, isDeleted=False).first()
        if student:
            pronouns = _pronouns_from_gender(student.gender)
            class_label = student.standardID.name if student.standardID_id and student.standardID else 'N/A'
            if student.standardID and student.standardID.section:
                class_label = f'{class_label} - {student.standardID.section}'
            recipient_name = student.name or 'Student'
            subtitle_name = class_label
            data.update({
                'student_name': recipient_name,
                'class_name': class_label,
                'father_name': student.parentID.fatherName if student.parentID_id and student.parentID else 'N/A',
                'mother_name': student.parentID.motherName if student.parentID_id and student.parentID else 'N/A',
                'roll_no': student.roll or 'N/A',
                'admission_no': student.registrationCode or 'N/A',
                'student_pronoun_subject': pronouns['subject'],
                'student_pronoun_object': pronouns['object'],
                'student_pronoun_subject_cap': pronouns['subject_cap'],
            })
        else:
            recipient_name = sample_student_name
            subtitle_name = sample_class_name
            data.update({
                'student_name': sample_student_name,
                'class_name': sample_class_name,
                'father_name': 'Rajesh Sharma',
                'mother_name': 'Sunita Sharma',
                'roll_no': '12',
                'admission_no': 'ADM-1024',
                'student_pronoun_subject': 'he',
                'student_pronoun_object': 'him',
                'student_pronoun_subject_cap': 'He',
            })
    elif recipient_category == 'student':
        recipient_name = sample_student_name
        subtitle_name = sample_class_name
        data.update({
            'student_name': sample_student_name,
            'class_name': sample_class_name,
            'father_name': 'Rajesh Sharma',
            'mother_name': 'Sunita Sharma',
            'roll_no': '12',
            'admission_no': 'ADM-1024',
            'student_pronoun_subject': 'he',
            'student_pronoun_object': 'him',
            'student_pronoun_subject_cap': 'He',
        })
    elif recipient_category in {'teacher', 'staff'} and recipient_id:
        teacher = TeacherDetail.objects.filter(pk=recipient_id, isDeleted=False).first()
        if teacher:
            pronouns = _pronouns_from_gender(teacher.gender)
            recipient_name = teacher.name or 'Teacher'
            subtitle_name = teacher.currentPosition or teacher.staffType or 'Faculty Member'
            data.update({
                'teacher_name': recipient_name,
                'designation': subtitle_name,
                'employee_code': teacher.employeeCode or 'N/A',
                'teacher_pronoun_subject': pronouns['subject'],
                'teacher_pronoun_object': pronouns['object'],
                'teacher_pronoun_subject_cap': pronouns['subject_cap'],
            })
        else:
            recipient_name = sample_teacher_name
            subtitle_name = sample_designation
            data.update({
                'teacher_name': sample_teacher_name,
                'designation': sample_designation,
                'employee_code': 'EMP-204',
                'teacher_pronoun_subject': 'she',
                'teacher_pronoun_object': 'her',
                'teacher_pronoun_subject_cap': 'She',
            })
    elif recipient_category in {'teacher', 'staff'}:
        recipient_name = sample_teacher_name
        subtitle_name = sample_designation
        data.update({
            'teacher_name': sample_teacher_name,
            'designation': sample_designation,
            'employee_code': 'EMP-204',
            'teacher_pronoun_subject': 'she',
            'teacher_pronoun_object': 'her',
            'teacher_pronoun_subject_cap': 'She',
        })
    elif recipient_category == 'parent' and recipient_id:
        parent = Parent.objects.filter(pk=recipient_id, isDeleted=False).first()
        if parent:
            recipient_name = parent.fatherName or 'Parent'
            subtitle_name = parent.phoneNumber or ''
            data.update({
                'parent_name': recipient_name,
                'father_name': parent.fatherName or '',
                'mother_name': parent.motherName or '',
            })
        else:
            recipient_name = 'Rajesh Sharma'
            subtitle_name = '9876543210'
            data.update({
                'parent_name': recipient_name,
                'father_name': 'Rajesh Sharma',
                'mother_name': 'Sunita Sharma',
            })
    elif recipient_category == 'parent':
        recipient_name = 'Rajesh Sharma'
        subtitle_name = '9876543210'
        data.update({
            'parent_name': recipient_name,
            'father_name': 'Rajesh Sharma',
            'mother_name': 'Sunita Sharma',
        })

    body_template = custom_body_text or certificate_type.defaultBodyTemplate or ''
    footer_template = custom_footer_text or certificate_type.defaultFooterText or ''
    title = custom_title or certificate_type.defaultTitle or certificate_type.name
    subtitle = custom_subtitle or certificate_type.defaultSubtitle or ''

    rendered_body = Template(body_template).safe_substitute(data)
    rendered_footer = Template(footer_template).safe_substitute(data)

    page_size = design.pageSize if design else 'A4'
    page_width_mm = Decimal('210')
    page_height_mm = Decimal('297')
    if design and design.pageSize == 'A5':
        page_width_mm = Decimal('148')
        page_height_mm = Decimal('210')
    elif design and design.pageSize == 'LETTER':
        page_width_mm = Decimal('216')
        page_height_mm = Decimal('279')
    elif design and design.pageSize == 'CUSTOM' and design.pageWidthMm and design.pageHeightMm:
        page_width_mm = design.pageWidthMm
        page_height_mm = design.pageHeightMm
    if design and design.pageSize == 'CUSTOM' and design.pageWidthMm and design.pageHeightMm:
        page_size = f'{design.pageWidthMm}mm {design.pageHeightMm}mm'

    orientation = design.orientation if design else 'portrait'
    display_page_width_mm = page_width_mm if orientation != 'landscape' else page_height_mm
    display_page_height_mm = page_height_mm if orientation != 'landscape' else page_width_mm
    margin_top = design.marginTopMm if design else Decimal('12')
    margin_right = design.marginRightMm if design else Decimal('12')
    margin_bottom = design.marginBottomMm if design else Decimal('12')
    margin_left = design.marginLeftMm if design else Decimal('12')
    content_width_mm = max(Decimal('20'), display_page_width_mm - margin_left - margin_right)
    content_height_mm = max(Decimal('20'), display_page_height_mm - margin_top - margin_bottom)

    context = {
        'issue': SimpleNamespace(
            schoolID=school,
            issueDate=issue_date,
            certificateNumber='Generated on final issue',
        ),
        'certificate_type': certificate_type,
        'design': design,
        'render_mode': 'preview',
        'design_font_stack': resolve_font_stack(design.fontFamily if design else 'Georgia'),
        'title': title,
        'subtitle': subtitle,
        'recipient_name': recipient_name,
        'recipient_subtitle': subtitle_name,
        'rendered_body': rendered_body,
        'rendered_footer': rendered_footer,
        'page_size_css': page_size,
        'margin_top': margin_top,
        'margin_right': margin_right,
        'margin_bottom': margin_bottom,
        'margin_left': margin_left,
        'page_width_mm': display_page_width_mm,
        'page_height_mm': display_page_height_mm,
        'content_width_mm': content_width_mm,
        'content_height_mm': content_height_mm,
        'scope_data': data,
    }
    context['resolved_overlay_items'] = resolve_overlay_items(
        design=design,
        scope_data=data,
        title=title,
        subtitle=subtitle,
        recipient_name=recipient_name,
        recipient_subtitle=subtitle_name,
        rendered_body=rendered_body,
        rendered_footer=rendered_footer,
    )
    context['is_image_overlay'] = bool(design and design.designMode == 'image_overlay')
    return context


def _sequence_prefix(*, certificate_type, session_obj=None):
    type_segment = slugify(certificate_type.slug if certificate_type else 'certificate').replace('-', '').upper()[:8] or 'CERT'
    session_segment = re.sub(r'[^0-9A-Za-z]', '', session_obj.sessionYear if session_obj else '')[:8]
    if session_segment:
        return f'{type_segment}-{session_segment}'
    return f'{type_segment}-{datetime.now().strftime("%Y")}'


def _new_verification_token():
    token = secrets.token_urlsafe(24)
    while CertificateIssue.objects.filter(verificationToken=token).exists():
        token = secrets.token_urlsafe(24)
    return token


def generate_certificate_number(*, school=None, session_obj=None, certificate_type=None):
    prefix = _sequence_prefix(certificate_type=certificate_type, session_obj=session_obj)
    with transaction.atomic():
        sequence, _ = CertificateSequence.objects.select_for_update().get_or_create(
            schoolID=school,
            sessionID=session_obj,
            certificateTypeID=certificate_type,
            prefix=prefix,
            defaults={'currentValue': 0},
        )
        sequence.currentValue += 1
        sequence.save(update_fields=['currentValue', 'lastUpdatedOn'])
        return f'{prefix}-{sequence.currentValue:04d}'


def preview_next_certificate_number(*, school=None, session_obj=None, certificate_type=None):
    prefix = _sequence_prefix(certificate_type=certificate_type, session_obj=session_obj)
    current_value = CertificateSequence.objects.filter(
        schoolID=school,
        sessionID=session_obj,
        certificateTypeID=certificate_type,
        prefix=prefix,
    ).values_list('currentValue', flat=True).first() or 0
    return f'{prefix}-{current_value + 1:04d}'


def create_certificate_issue(*, request, certificate_type, design, recipient_id=None, issue_date=None, custom_title='', custom_subtitle='', custom_body_text='', custom_footer_text=''):
    school, session_obj = get_request_scope(request)
    issue = CertificateIssue(
        certificateTypeID=certificate_type,
        certificateDesignID=design,
        schoolID=school,
        sessionID=session_obj,
        recipientCategory=certificate_type.recipientCategory,
        issueDate=issue_date or date.today(),
        certificateNumber=generate_certificate_number(
            school=school,
            session_obj=session_obj,
            certificate_type=certificate_type,
        ),
        verificationToken=_new_verification_token(),
        customTitle=custom_title or None,
        customSubtitle=custom_subtitle or None,
        customBodyText=custom_body_text or None,
        customFooterText=custom_footer_text or None,
        lastEditedBy=_user_label(request.user),
        updatedByUserID=request.user,
    )

    if certificate_type.recipientCategory == 'student' and recipient_id:
        issue.studentID = Student.objects.filter(pk=recipient_id, isDeleted=False).first()
    elif certificate_type.recipientCategory in {'teacher', 'staff'} and recipient_id:
        issue.teacherID = TeacherDetail.objects.filter(pk=recipient_id, isDeleted=False).first()
    elif certificate_type.recipientCategory == 'parent' and recipient_id:
        issue.parentID = Parent.objects.filter(pk=recipient_id, isDeleted=False).first()

    issue.save()
    issue_context = build_issue_context(issue, request=request)
    issue.contextSnapshot = issue_context.get('scope_data', {})
    issue.issueData = issue_context.get('scope_data', {})
    issue.designSnapshot = {
        'id': design.id,
        'name': design.name,
        'slug': design.slug,
        'designMode': design.designMode,
        'templateKey': design.templateKey,
        'pageSize': design.pageSize,
        'orientation': design.orientation,
        'designVersion': getattr(design, 'designVersion', 1),
    }
    issue.renderSnapshot = {
        'title': issue_context.get('title', ''),
        'subtitle': issue_context.get('subtitle', ''),
        'recipient_name': issue_context.get('recipient_name', ''),
        'recipient_subtitle': issue_context.get('recipient_subtitle', ''),
        'verification_url': issue_context.get('verification_url', ''),
    }
    issue.htmlSnapshot = render_certificate_document(issue_context, request=request)
    issue.save(update_fields=['contextSnapshot', 'issueData', 'designSnapshot', 'renderSnapshot', 'htmlSnapshot', 'lastUpdatedOn'])
    return issue


def create_certificate_issues_bulk(*, request, certificate_type, design, recipient_ids=None, issue_date=None, custom_title='', custom_subtitle='', custom_body_text='', custom_footer_text=''):
    cleaned_recipient_ids = []
    for recipient_id in recipient_ids or []:
        value = str(recipient_id or '').strip()
        if value and value not in cleaned_recipient_ids:
            cleaned_recipient_ids.append(value)

    if certificate_type.recipientCategory == 'school':
        cleaned_recipient_ids = [None]

    issues = []
    with transaction.atomic():
        for recipient_id in cleaned_recipient_ids:
            issue = create_certificate_issue(
                request=request,
                certificate_type=certificate_type,
                design=design,
                recipient_id=recipient_id,
                issue_date=issue_date,
                custom_title=custom_title,
                custom_subtitle=custom_subtitle,
                custom_body_text=custom_body_text,
                custom_footer_text=custom_footer_text,
            )
            issues.append(issue)
    return issues


def cancel_certificate_issue(*, issue, request, reason=''):
    if issue.issueStatus == 'cancelled':
        return issue
    issue.issueStatus = 'cancelled'
    issue.cancelledOn = timezone.now()
    issue.cancelledByUserID = request.user if getattr(request, 'user', None) and request.user.is_authenticated else None
    issue.cancellationReason = (reason or '').strip() or 'Cancelled by authorized user.'
    issue.lastEditedBy = _user_label(getattr(request, 'user', None))
    issue.updatedByUserID = issue.cancelledByUserID
    issue.save(update_fields=[
        'issueStatus',
        'cancelledOn',
        'cancelledByUserID',
        'cancellationReason',
        'lastEditedBy',
        'updatedByUserID',
        'lastUpdatedOn',
    ])
    return issue


def reissue_certificate_issue(*, issue, request, reason=''):
    if issue.issueStatus == 'issued':
        issue.issueStatus = 'reissued'
        issue.cancelledOn = timezone.now()
        issue.cancelledByUserID = request.user if getattr(request, 'user', None) and request.user.is_authenticated else None
        issue.cancellationReason = (reason or '').strip() or 'Reissued with a new certificate number.'
        issue.lastEditedBy = _user_label(getattr(request, 'user', None))
        issue.updatedByUserID = issue.cancelledByUserID
        issue.save(update_fields=[
            'issueStatus',
            'cancelledOn',
            'cancelledByUserID',
            'cancellationReason',
            'lastEditedBy',
            'updatedByUserID',
            'lastUpdatedOn',
        ])
    recipient_id = None
    if issue.recipientCategory == 'student':
        recipient_id = issue.studentID_id
    elif issue.recipientCategory in {'teacher', 'staff'}:
        recipient_id = issue.teacherID_id
    elif issue.recipientCategory == 'parent':
        recipient_id = issue.parentID_id
    new_issue = create_certificate_issue(
        request=request,
        certificate_type=issue.certificateTypeID,
        design=issue.certificateDesignID,
        recipient_id=recipient_id,
        issue_date=date.today(),
        custom_title=issue.customTitle or '',
        custom_subtitle=issue.customSubtitle or '',
        custom_body_text=issue.customBodyText or '',
        custom_footer_text=issue.customFooterText or '',
    )
    new_issue.reissuedFromIssueID = issue
    new_issue.issuePayload = {
        **(new_issue.issuePayload or {}),
        'reissue_reason': (reason or '').strip(),
        'reissued_from_certificate_number': issue.certificateNumber,
    }
    new_issue.save(update_fields=['reissuedFromIssueID', 'issuePayload', 'lastUpdatedOn'])
    return new_issue


def build_preview_urls(issue):
    return {
        'preview_url': reverse('certificateApp:issue_preview', kwargs={'issue_id': issue.id}),
        'print_url': reverse('certificateApp:issue_print', kwargs={'issue_id': issue.id}),
        'download_url': reverse('certificateApp:issue_download_pdf', kwargs={'issue_id': issue.id}),
        'verify_url': reverse('certificateApp:verify_certificate', kwargs={'token': issue.verificationToken}) if issue.verificationToken else '',
    }


def create_custom_design(*, request, certificate_type, cleaned_data):
    school, _ = get_request_scope(request)
    name = cleaned_data['name'].strip() or 'Untitled Certificate Design'
    status = cleaned_data.get('status') or 'published'
    if status not in {'draft', 'published'}:
        status = 'published'
    return CertificateDesign.objects.create(
        certificateTypeID=certificate_type,
        schoolID=school,
        name=name,
        slug=_unique_design_slug(
            certificate_type=certificate_type,
            school_id=school.id if school else None,
            base_name=name,
        ),
        designMode=cleaned_data.get('design_mode') or 'html',
        templateKey='custom',
        pageSize=cleaned_data['page_size'],
        orientation=cleaned_data['orientation'],
        pageWidthMm=cleaned_data.get('page_width_mm') or None,
        pageHeightMm=cleaned_data.get('page_height_mm') or None,
        marginTopMm=cleaned_data.get('margin_top_mm') or Decimal('12'),
        marginRightMm=cleaned_data.get('margin_right_mm') or Decimal('12'),
        marginBottomMm=cleaned_data.get('margin_bottom_mm') or Decimal('12'),
        marginLeftMm=cleaned_data.get('margin_left_mm') or Decimal('12'),
        titleAlignment=cleaned_data.get('title_alignment') or 'center',
        bodyAlignment=cleaned_data.get('body_alignment') or 'center',
        borderStyle=cleaned_data.get('border_style') or 'single',
        fontFamily=cleaned_data.get('font_family') or 'Georgia',
        accentColor=cleaned_data.get('accent_color') or '#1d4ed8',
        textColor=cleaned_data.get('text_color') or '#1f2937',
        backgroundColor=cleaned_data.get('background_color') or '#ffffff',
        backgroundImage=cleaned_data.get('background_image'),
        customHeaderText=cleaned_data.get('custom_header_text') or None,
        customFooterText=cleaned_data.get('custom_footer_text') or None,
        customCss=cleaned_data.get('custom_css') or None,
        overlaySchema=normalize_overlay_schema(cleaned_data.get('overlay_schema') or []),
        showLogo=bool(cleaned_data.get('show_logo')),
        showSignatureLine=bool(cleaned_data.get('show_signature_line')),
        showSeal=bool(cleaned_data.get('show_seal')),
        isDraft=status == 'draft',
        status=status,
        isActive=status == 'published',
        isCustom=True,
        lastEditedBy=_user_label(request.user),
        updatedByUserID=request.user,
    )


def validate_design_for_publish(*, cleaned_data, existing_design=None):
    errors = []
    name = (cleaned_data.get('name') or '').strip()
    if not name:
        errors.append('Design name is required before publishing.')

    for field_name, label in [
        ('accent_color', 'Accent color'),
        ('text_color', 'Text color'),
        ('background_color', 'Background color'),
    ]:
        value = str(cleaned_data.get(field_name) or '').strip()
        if not re.match(r'^#[0-9a-fA-F]{6}$', value):
            errors.append(f'{label} must be a valid hex color.')

    if cleaned_data.get('page_size') == 'CUSTOM':
        try:
            width = Decimal(str(cleaned_data.get('page_width_mm') or '0'))
            height = Decimal(str(cleaned_data.get('page_height_mm') or '0'))
        except Exception:
            width = height = Decimal('0')
        if width < Decimal('80') or height < Decimal('80'):
            errors.append('Custom paper size must include width and height of at least 80mm.')

    for field_name, label in [
        ('margin_top_mm', 'Top margin'),
        ('margin_right_mm', 'Right margin'),
        ('margin_bottom_mm', 'Bottom margin'),
        ('margin_left_mm', 'Left margin'),
    ]:
        try:
            value = Decimal(str(cleaned_data.get(field_name) or '0'))
        except Exception:
            value = Decimal('-1')
        if value < 0 or value > 60:
            errors.append(f'{label} must be between 0mm and 60mm.')

    if cleaned_data.get('design_mode') == 'image_overlay':
        overlay_items = normalize_overlay_schema(cleaned_data.get('overlay_schema') or [])
        has_background = bool(cleaned_data.get('background_image') or (existing_design and existing_design.backgroundImage))
        if not has_background:
            errors.append('Image overlay designs need a background artwork before publishing.')
        if not overlay_items:
            errors.append('Image overlay designs need at least one overlay field before publishing.')

    return errors


def update_custom_design(*, request, design, certificate_type, cleaned_data):
    status = cleaned_data.get('status') or 'published'
    if status not in {'draft', 'published'}:
        status = 'published'

    design.certificateTypeID = certificate_type
    design.name = cleaned_data['name'].strip() or 'Untitled Certificate Design'
    design.designMode = cleaned_data.get('design_mode') or 'html'
    design.pageSize = cleaned_data['page_size']
    design.orientation = cleaned_data['orientation']
    design.pageWidthMm = cleaned_data.get('page_width_mm') or None
    design.pageHeightMm = cleaned_data.get('page_height_mm') or None
    design.marginTopMm = cleaned_data.get('margin_top_mm') or Decimal('12')
    design.marginRightMm = cleaned_data.get('margin_right_mm') or Decimal('12')
    design.marginBottomMm = cleaned_data.get('margin_bottom_mm') or Decimal('12')
    design.marginLeftMm = cleaned_data.get('margin_left_mm') or Decimal('12')
    design.titleAlignment = cleaned_data.get('title_alignment') or 'center'
    design.bodyAlignment = cleaned_data.get('body_alignment') or 'center'
    design.borderStyle = cleaned_data.get('border_style') or 'single'
    design.fontFamily = cleaned_data.get('font_family') or 'Georgia'
    design.accentColor = cleaned_data.get('accent_color') or '#1d4ed8'
    design.textColor = cleaned_data.get('text_color') or '#1f2937'
    design.backgroundColor = cleaned_data.get('background_color') or '#ffffff'
    if cleaned_data.get('background_image'):
        design.backgroundImage = cleaned_data.get('background_image')
    design.customHeaderText = cleaned_data.get('custom_header_text') or None
    design.customFooterText = cleaned_data.get('custom_footer_text') or None
    design.customCss = cleaned_data.get('custom_css') or None
    design.overlaySchema = normalize_overlay_schema(cleaned_data.get('overlay_schema') or [])
    design.showLogo = bool(cleaned_data.get('show_logo'))
    design.showSignatureLine = bool(cleaned_data.get('show_signature_line'))
    design.showSeal = bool(cleaned_data.get('show_seal'))
    design.isDraft = status == 'draft'
    design.status = status
    design.isActive = status == 'published'
    design.isCustom = True
    design.isSystem = False
    design.designVersion = int(design.designVersion or 1) + 1
    design.lastEditedBy = _user_label(request.user)
    design.updatedByUserID = request.user
    design.save()
    return design


def duplicate_certificate_design(*, request, source_design, name=None, save_as_draft=True):
    school, _ = get_request_scope(request)
    source_name = name or f'{source_design.name} Copy'
    status = 'draft' if save_as_draft else 'published'
    duplicated = CertificateDesign.objects.create(
        certificateTypeID=source_design.certificateTypeID,
        schoolID=school,
        name=source_name,
        slug=_unique_design_slug(
            certificate_type=source_design.certificateTypeID,
            school_id=school.id if school else None,
            base_name=source_name,
        ),
        designMode=source_design.designMode,
        templateKey=source_design.templateKey,
        pageSize=source_design.pageSize,
        orientation=source_design.orientation,
        pageWidthMm=source_design.pageWidthMm,
        pageHeightMm=source_design.pageHeightMm,
        marginTopMm=source_design.marginTopMm,
        marginRightMm=source_design.marginRightMm,
        marginBottomMm=source_design.marginBottomMm,
        marginLeftMm=source_design.marginLeftMm,
        titleAlignment=source_design.titleAlignment,
        bodyAlignment=source_design.bodyAlignment,
        borderStyle=source_design.borderStyle,
        fontFamily=source_design.fontFamily,
        accentColor=source_design.accentColor,
        textColor=source_design.textColor,
        backgroundColor=source_design.backgroundColor,
        backgroundImage=source_design.backgroundImage,
        assetConfig=source_design.assetConfig or {},
        layoutConfig=source_design.layoutConfig or {},
        themeConfig=source_design.themeConfig or {},
        designSchema=source_design.designSchema or {},
        designJson=source_design.designJson or {},
        mergeSchema=source_design.mergeSchema or [],
        basedOnDesignID=source_design,
        designVersion=max(1, int(source_design.designVersion or 1)),
        customHeaderText=source_design.customHeaderText,
        customFooterText=source_design.customFooterText,
        customCss=source_design.customCss,
        overlaySchema=source_design.overlaySchema or [],
        showLogo=source_design.showLogo,
        showSignatureLine=source_design.showSignatureLine,
        showSeal=source_design.showSeal,
        isDraft=status == 'draft',
        isDefaultForType=False,
        isActive=status == 'published',
        status=status,
        isSystem=False,
        isCustom=True,
        lastEditedBy=_user_label(request.user),
        updatedByUserID=request.user,
    )
    return duplicated
