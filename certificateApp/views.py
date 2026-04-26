from django.http import HttpResponse
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from urllib.parse import urlencode
from datetime import datetime
from types import SimpleNamespace
import json

from homeApp.utils import login_required
from utils.custom_decorators import check_groups

from .models import CertificateDesign, CertificateIssue, CertificateType
from .services import (
    build_issue_context,
    build_issue_pdf,
    build_preview_urls,
    build_generator_preview_context,
    create_custom_design,
    ensure_system_certificate_defaults,
    get_certificate_designs,
    get_recipient_options,
    get_certificate_types,
    get_request_scope,
)


@login_required
@check_groups('Admin', 'Owner')
def dashboard(request):
    school, session_obj = get_request_scope(request)
    ensure_system_certificate_defaults(school_id=school.id if school else None, user_obj=request.user)

    issues = CertificateIssue.objects.filter(
        schoolID=school,
        sessionID=session_obj,
        isDeleted=False,
    ).select_related('certificateTypeID', 'certificateDesignID').order_by('-issueDate', '-id')

    context = {
        'certificate_types_count': get_certificate_types(school_id=school.id if school else None).count(),
        'certificate_designs_count': CertificateDesign.objects.filter(schoolID=school, isDeleted=False, isActive=True).count(),
        'certificate_issues_count': issues.count(),
        'recent_issues': issues[:8],
        'all_issues': issues,
        'school': school,
    }
    return render(request, 'certificateApp/dashboard.html', context)


@login_required
@check_groups('Admin', 'Owner')
def design_library(request):
    school, _ = get_request_scope(request)
    ensure_system_certificate_defaults(school_id=school.id if school else None, user_obj=request.user)

    def infer_purpose_tags(cert_type, design):
        text = ' '.join([
            cert_type.name or '',
            cert_type.description or '',
            design.name or '',
            design.customHeaderText or '',
            design.customFooterText or '',
            design.templateKey or '',
        ]).lower()
        tags = []
        if any(token in text for token in ['sports', 'athletic', 'championship', 'house']):
            tags.append('sports')
        if any(token in text for token in ['festival', 'cultural', 'music', 'arts', 'ceremonial', 'celebration']):
            tags.append('festival')
        if any(token in text for token in ['staff', 'teacher', 'service', 'experience', 'employment']):
            tags.append('staff')
        if any(token in text for token in ['merit', 'honor', 'honours', 'board', 'rank', 'distinction', 'scholar']):
            tags.append('merit')
        if any(token in text for token in ['record', 'ledger', 'attendance', 'transfer', 'fee', 'bonafide', 'archive', 'registry']):
            tags.append('records')
        if not tags:
            tags.append('general')
        return tags

    types = list(get_certificate_types(school_id=school.id if school else None))
    design_map = {}

    def build_design_signature(design):
        if design.isCustom or design.designMode == 'image_overlay':
            return ('design', design.id)
        return (
            'family',
            design.templateKey,
            design.designMode,
            design.pageSize,
            design.orientation,
            design.borderStyle,
            design.titleAlignment,
            design.bodyAlignment,
            design.fontFamily or '',
            design.accentColor or '',
            design.textColor or '',
            design.backgroundColor or '',
            bool(design.showLogo),
            bool(design.showSeal),
            bool(design.showSignatureLine),
            design.customHeaderText or '',
            design.customFooterText or '',
        )

    for cert_type in types:
        for design in get_certificate_designs(certificate_type=cert_type, school_id=school.id if school else None):
            signature = build_design_signature(design)
            entry = design_map.setdefault(signature, {
                'design': design,
                'sample_type': cert_type,
                'certificate_types': [],
                'certificate_type_names': [],
                'purpose_tags': set(),
            })
            if cert_type.id not in [item.id for item in entry['certificate_types']]:
                entry['certificate_types'].append(cert_type)
                entry['certificate_type_names'].append(cert_type.name)
            entry['purpose_tags'].update(infer_purpose_tags(cert_type, design))
            if design.isCustom and not entry['design'].isCustom:
                entry['design'] = design
                entry['sample_type'] = cert_type

    catalog_entries = []
    for entry in design_map.values():
        design = entry['design']
        type_names = entry['certificate_type_names']
        purpose_tags = sorted(entry['purpose_tags'])
        support_preview = ', '.join(type_names[:3])
        if len(type_names) > 3:
            support_preview += f' +{len(type_names) - 3} more'

        if design.designMode == 'image_overlay':
            section_key = 'artwork'
            section_title = 'Imported Artwork Bases'
            section_copy = 'Photo, scan, or PDF-based certificate layouts that preserve artwork and place editable fields on top.'
            scope_label = 'Artwork Base'
        elif design.templateKey in {'hand_fill_form', 'prize_day_form'}:
            section_key = 'forms'
            section_title = 'Write-In Forms'
            section_copy = 'Manual write-in and event-day certificate sheets built for fast practical issuing.'
            scope_label = 'Write-In Form'
        elif design.isCustom:
            section_key = 'school'
            section_title = 'School Originals'
            section_copy = 'School-specific designs shaped around local identity, ceremony style, and institutional needs.'
            scope_label = 'School Original'
        else:
            section_key = 'shared'
            section_title = 'Shared Template Families'
            section_copy = 'Reusable system design families surfaced once instead of repeated for every certificate type.'
            scope_label = 'Shared Family'

        catalog_entries.append(SimpleNamespace(
            section_key=section_key,
            section_title=section_title,
            section_copy=section_copy,
            design=design,
            sample_type=entry['sample_type'],
            certificate_types=entry['certificate_types'],
            certificate_type_names=type_names,
            certificate_type_count=len(type_names),
            support_preview=support_preview,
            purpose_tags=purpose_tags or ['general'],
            purpose_label=' / '.join(tag.title() for tag in (purpose_tags or ['general'])[:2]),
            scope_label=scope_label,
        ))

    section_order = ['school', 'artwork', 'forms', 'shared']
    section_map = {key: [] for key in section_order}
    for entry in catalog_entries:
        section_map[entry.section_key].append(entry)

    design_sections = []
    for key in section_order:
        rows = sorted(section_map[key], key=lambda item: ((0 if item.design.isCustom else 1), item.design.name.lower()))
        if not rows:
            continue
        design_sections.append(SimpleNamespace(
            key=key,
            title=rows[0].section_title,
            copy=rows[0].section_copy,
            entries=rows,
        ))

    return render(request, 'certificateApp/design_library.html', {
        'design_sections': design_sections,
        'unique_design_count': len(catalog_entries),
        'school': school,
    })


@login_required
@check_groups('Admin', 'Owner')
def create_design(request):
    school, _ = get_request_scope(request)
    ensure_system_certificate_defaults(school_id=school.id if school else None, user_obj=request.user)
    types = get_certificate_types(school_id=school.id if school else None)

    if request.method == 'POST':
        certificate_type = get_object_or_404(CertificateType, pk=request.POST.get('certificate_type_id'), isDeleted=False)
        design = create_custom_design(
            request=request,
            certificate_type=certificate_type,
            cleaned_data={
                'name': request.POST.get('name', ''),
                'design_mode': request.POST.get('design_mode', 'html'),
                'page_size': request.POST.get('page_size', 'A4'),
                'orientation': request.POST.get('orientation', 'portrait'),
                'page_width_mm': request.POST.get('page_width_mm') or None,
                'page_height_mm': request.POST.get('page_height_mm') or None,
                'margin_top_mm': request.POST.get('margin_top_mm') or 12,
                'margin_right_mm': request.POST.get('margin_right_mm') or 12,
                'margin_bottom_mm': request.POST.get('margin_bottom_mm') or 12,
                'margin_left_mm': request.POST.get('margin_left_mm') or 12,
                'title_alignment': request.POST.get('title_alignment', 'center'),
                'body_alignment': request.POST.get('body_alignment', 'center'),
                'border_style': request.POST.get('border_style', 'single'),
                'font_family': request.POST.get('font_family', 'Georgia'),
                'accent_color': request.POST.get('accent_color', '#1d4ed8'),
                'text_color': request.POST.get('text_color', '#1f2937'),
                'background_color': request.POST.get('background_color', '#ffffff'),
                'background_image': request.FILES.get('background_image'),
                'custom_header_text': request.POST.get('custom_header_text', ''),
                'custom_footer_text': request.POST.get('custom_footer_text', ''),
                'custom_css': request.POST.get('custom_css', ''),
                'overlay_schema': request.POST.get('overlay_schema_json', '[]'),
                'show_logo': request.POST.get('show_logo'),
                'show_signature_line': request.POST.get('show_signature_line'),
                'show_seal': request.POST.get('show_seal'),
            },
        )
        return redirect('certificateApp:design_detail', design_id=design.id)

    return render(request, 'certificateApp/create_design.html', {'certificate_types': types, 'school': school})


@login_required
@check_groups('Admin', 'Owner')
def design_detail(request, design_id):
    school, _ = get_request_scope(request)
    design = get_object_or_404(CertificateDesign.objects.select_related('certificateTypeID'), pk=design_id, isDeleted=False)
    preview_context = build_generator_preview_context(
        request=request,
        certificate_type=design.certificateTypeID,
        design=design,
        custom_title=design.certificateTypeID.defaultTitle or design.name,
        custom_subtitle=design.certificateTypeID.defaultSubtitle or '',
        custom_body_text=design.certificateTypeID.defaultBodyTemplate or '',
        custom_footer_text=design.customFooterText or design.certificateTypeID.defaultFooterText or '',
    )
    preview_context['render_mode'] = 'pdf'
    preview_context['issue'].certificateNumber = 'CERT-20260424-0012'
    return render(request, 'certificateApp/design_detail.html', {
        'design': design,
        'preview_school': design.schoolID or school,
        'detail_preview_context': preview_context,
    })


@login_required
@check_groups('Admin', 'Owner')
def generator(request):
    school, session_obj = get_request_scope(request)
    ensure_system_certificate_defaults(school_id=school.id if school else None, user_obj=request.user)
    certificate_types = get_certificate_types(school_id=school.id if school else None)
    preselected_design = None
    preselected_type = None
    design_id = request.GET.get('design')
    if design_id:
        preselected_design = CertificateDesign.objects.filter(
            Q(schoolID=school) | Q(schoolID__isnull=True),
            pk=design_id,
            isDeleted=False,
            isActive=True,
        ).select_related('certificateTypeID').first()
        if preselected_design:
            preselected_type = preselected_design.certificateTypeID
    initial_live_preview_url = ''
    initial_generator_meta_json = 'null'
    initial_exact_preview_context = None
    if preselected_design and preselected_type:
        initial_live_preview_url = '{}?{}'.format(
            reverse('certificateApp:generator_live_preview'),
            urlencode({
                'certificate_type_id': preselected_type.id,
                'design_id': preselected_design.id,
            }),
        )
        initial_exact_preview_context = build_generator_preview_context(
            request=request,
            certificate_type=preselected_type,
            design=preselected_design,
            custom_title=preselected_type.defaultTitle or preselected_type.name,
            custom_subtitle=preselected_type.defaultSubtitle or '',
            custom_body_text=preselected_type.defaultBodyTemplate or '',
            custom_footer_text=preselected_design.customFooterText or preselected_type.defaultFooterText or '',
        )
        initial_exact_preview_context['render_mode'] = 'pdf'
        initial_exact_preview_context['issue'].certificateNumber = 'Generated on final issue'
        designs = get_certificate_designs(
            certificate_type=preselected_type,
            school_id=school.id if school else None,
        )
        recipients = get_recipient_options(
            recipient_category=preselected_type.recipientCategory,
            school_id=school.id if school else None,
            session_id=session_obj.id if session_obj else None,
        )
        initial_generator_meta_json = json.dumps({
            'recipientCategory': preselected_type.recipientCategory,
            'defaultTitle': preselected_type.defaultTitle or preselected_type.name,
            'defaultSubtitle': preselected_type.defaultSubtitle or '',
            'defaultBodyTemplate': preselected_type.defaultBodyTemplate or '',
            'defaultFooterText': preselected_type.defaultFooterText or '',
            'designs': [
                {
                    'id': design.id,
                    'name': design.name,
                    'designMode': design.designMode,
                    'templateKey': design.templateKey,
                    'customHeaderText': design.customHeaderText or '',
                    'customFooterText': design.customFooterText or '',
                    'customCss': design.customCss or '',
                    'pageSize': design.pageSize,
                    'pageWidthMm': float(design.pageWidthMm) if design.pageWidthMm else None,
                    'pageHeightMm': float(design.pageHeightMm) if design.pageHeightMm else None,
                    'orientation': design.orientation,
                    'marginTopMm': float(design.marginTopMm or 12),
                    'marginRightMm': float(design.marginRightMm or 12),
                    'marginBottomMm': float(design.marginBottomMm or 12),
                    'marginLeftMm': float(design.marginLeftMm or 12),
                    'accentColor': design.accentColor,
                    'textColor': design.textColor,
                    'backgroundColor': design.backgroundColor,
                    'fontFamily': design.fontFamily,
                    'titleAlignment': design.titleAlignment,
                    'bodyAlignment': design.bodyAlignment,
                    'borderStyle': design.borderStyle,
                    'showLogo': bool(design.showLogo),
                    'showSeal': bool(design.showSeal),
                    'showSignatureLine': bool(design.showSignatureLine),
                    'backgroundImageUrl': design.backgroundImage.url if design.backgroundImage else '',
                    'overlaySchema': design.overlaySchema or [],
                    'isCustom': design.isCustom,
                }
                for design in designs
            ],
            'recipients': recipients,
        })

    return render(request, 'certificateApp/generator.html', {
        'certificate_types': certificate_types,
        'preselected_design_id': preselected_design.id if preselected_design else '',
        'preselected_type_id': preselected_type.id if preselected_type else '',
        'preselected_design': preselected_design,
        'preselected_type': preselected_type,
        'initial_live_preview_url': initial_live_preview_url,
        'initial_generator_meta_json': initial_generator_meta_json,
        'initial_exact_preview_context': initial_exact_preview_context,
        'school': school,
    })


@login_required
@check_groups('Admin', 'Owner')
def generator_live_preview(request):
    school, _ = get_request_scope(request)
    certificate_type_id = request.GET.get('certificate_type_id')
    design_id = request.GET.get('design_id')
    certificate_type = CertificateType.objects.filter(
        Q(schoolID=school) | Q(schoolID__isnull=True),
        pk=certificate_type_id,
        isDeleted=False,
        isActive=True,
    ).first()
    design = CertificateDesign.objects.filter(
        Q(schoolID=school) | Q(schoolID__isnull=True),
        pk=design_id,
        isDeleted=False,
        isActive=True,
    ).select_related('certificateTypeID').first()

    if not certificate_type or not design or design.certificateTypeID_id != certificate_type.id:
        empty_context = {
            'is_empty_preview': True,
            'title': 'Certificate Preview',
        }
        if request.GET.get('embed') == '1':
            return render(request, 'certificateApp/generator_live_preview_embed.html', empty_context)
        return render(request, 'certificateApp/generator_live_preview.html', empty_context)

    issue_date = None
    issue_date_raw = request.GET.get('issue_date', '')
    if issue_date_raw:
        try:
            issue_date = datetime.strptime(issue_date_raw, '%Y-%m-%d').date()
        except ValueError:
            issue_date = None

    context = build_generator_preview_context(
        request=request,
        certificate_type=certificate_type,
        design=design,
        recipient_id=request.GET.get('recipient_id') or None,
        issue_date=issue_date,
        custom_title=request.GET.get('custom_title', ''),
        custom_subtitle=request.GET.get('custom_subtitle', ''),
        custom_body_text=request.GET.get('custom_body_text', ''),
        custom_footer_text=request.GET.get('custom_footer_text', ''),
    )
    context['render_mode'] = 'pdf'
    context['page_title'] = f'{design.name} Preview'
    if request.GET.get('embed') == '1':
        return render(request, 'certificateApp/generator_live_preview_embed.html', context)
    return render(request, 'certificateApp/generator_live_preview.html', context)


@login_required
@check_groups('Admin', 'Owner')
def issue_preview(request, issue_id):
    issue = get_object_or_404(
        CertificateIssue.objects.select_related(
            'certificateTypeID', 'certificateDesignID', 'schoolID', 'sessionID', 'studentID', 'teacherID', 'parentID'
        ),
        pk=issue_id,
        isDeleted=False,
    )
    context = build_issue_context(issue)
    context['preview_urls'] = build_preview_urls(issue)
    return render(request, 'certificateApp/issue_preview.html', context)


@login_required
@check_groups('Admin', 'Owner')
def issue_print(request, issue_id):
    issue = get_object_or_404(
        CertificateIssue.objects.select_related(
            'certificateTypeID', 'certificateDesignID', 'schoolID', 'sessionID', 'studentID', 'teacherID', 'parentID'
        ),
        pk=issue_id,
        isDeleted=False,
    )
    context = build_issue_context(issue)
    context['render_mode'] = 'print'
    return render(request, 'certificateApp/issue_print.html', context)


@login_required
@check_groups('Admin', 'Owner')
def issue_download_pdf(request, issue_id):
    issue = get_object_or_404(CertificateIssue, pk=issue_id, isDeleted=False)
    response = HttpResponse(build_issue_pdf(issue), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{issue.certificateNumber}.pdf"'
    return response
