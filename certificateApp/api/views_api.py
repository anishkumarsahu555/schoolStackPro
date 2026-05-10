from datetime import datetime

from django.http import JsonResponse
from django.shortcuts import get_object_or_404

from homeApp.utils import login_required
from utils.custom_decorators import check_groups
from utils.custom_response import ErrorResponse, SuccessResponse

from certificateApp.models import CertificateDesign, CertificateType
from certificateApp.services import (
    build_preview_urls,
    create_certificate_issue,
    create_certificate_issues_bulk,
    ensure_system_certificate_defaults,
    get_certificate_designs,
    get_recipient_options,
    get_request_scope,
    preview_next_certificate_number,
)


@login_required
@check_groups('Admin', 'Owner')
def get_certificate_generator_meta_api(request):
    school, session_obj = get_request_scope(request)
    ensure_system_certificate_defaults(school_id=school.id if school else None, user_obj=request.user)
    certificate_type = get_object_or_404(CertificateType, pk=request.GET.get('certificate_type_id'), isDeleted=False)
    designs = get_certificate_designs(certificate_type=certificate_type, school_id=school.id if school else None)
    recipients = get_recipient_options(
        recipient_category=certificate_type.recipientCategory,
        school_id=school.id if school else None,
        session_id=session_obj.id if session_obj else None,
    )
    return JsonResponse({
        'success': True,
        'data': {
            'recipientCategory': certificate_type.recipientCategory,
            'defaultTitle': certificate_type.defaultTitle or certificate_type.name,
            'defaultSubtitle': certificate_type.defaultSubtitle or '',
            'defaultBodyTemplate': certificate_type.defaultBodyTemplate or '',
            'defaultFooterText': certificate_type.defaultFooterText or '',
            'nextCertificateNumber': preview_next_certificate_number(
                school=school,
                session_obj=session_obj,
                certificate_type=certificate_type,
            ),
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
                    'layoutConfig': design.layoutConfig or {},
                    'qrPosition': (design.layoutConfig or {}).get('qr_position', 'center'),
                    'signaturePosition': (design.layoutConfig or {}).get('signature_position', 'right'),
                    'sealPosition': (design.layoutConfig or {}).get('seal_position', 'left'),
                    'backgroundImageUrl': design.backgroundImage.url if design.backgroundImage else '',
                    'overlaySchema': design.overlaySchema or [],
                    'isCustom': design.isCustom,
                    'isDefaultForType': design.isDefaultForType,
                }
                for design in designs
            ],
            'recipients': recipients,
        },
    })


@login_required
@check_groups('Admin', 'Owner')
def create_certificate_issue_api(request):
    if request.method != 'POST':
        return ErrorResponse('Invalid request method.', status_code=405).to_json_response()

    certificate_type = get_object_or_404(CertificateType, pk=request.POST.get('certificate_type_id'), isDeleted=False)
    design = get_object_or_404(
        CertificateDesign,
        pk=request.POST.get('design_id'),
        certificateTypeID=certificate_type,
        isDeleted=False,
    )
    issue_date_raw = request.POST.get('issue_date', '')
    try:
        issue_date = datetime.strptime(issue_date_raw, '%Y-%m-%d').date() if issue_date_raw else None
    except ValueError:
        return ErrorResponse('Issue date is invalid.', status_code=400).to_json_response()

    recipient_id = request.POST.get('recipient_id') or None
    if certificate_type.recipientCategory != 'school' and not recipient_id:
        return ErrorResponse('Please select a recipient.', status_code=400).to_json_response()

    issue = create_certificate_issue(
        request=request,
        certificate_type=certificate_type,
        design=design,
        recipient_id=recipient_id,
        issue_date=issue_date,
        custom_title=request.POST.get('custom_title', ''),
        custom_subtitle=request.POST.get('custom_subtitle', ''),
        custom_body_text=request.POST.get('custom_body_text', ''),
        custom_footer_text=request.POST.get('custom_footer_text', ''),
    )
    return SuccessResponse(
        'Certificate generated successfully.',
        data=build_preview_urls(issue),
    ).to_json_response()


@login_required
@check_groups('Admin', 'Owner')
def create_certificate_issues_bulk_api(request):
    if request.method != 'POST':
        return ErrorResponse('Invalid request method.', status_code=405).to_json_response()

    certificate_type = get_object_or_404(CertificateType, pk=request.POST.get('certificate_type_id'), isDeleted=False)
    design = get_object_or_404(
        CertificateDesign,
        pk=request.POST.get('design_id'),
        certificateTypeID=certificate_type,
        isDeleted=False,
    )
    issue_date_raw = request.POST.get('issue_date', '')
    try:
        issue_date = datetime.strptime(issue_date_raw, '%Y-%m-%d').date() if issue_date_raw else None
    except ValueError:
        return ErrorResponse('Issue date is invalid.', status_code=400).to_json_response()

    recipient_ids = request.POST.getlist('recipient_ids[]') or request.POST.getlist('recipient_ids')
    if certificate_type.recipientCategory != 'school' and not recipient_ids:
        return ErrorResponse('Please select at least one recipient.', status_code=400).to_json_response()
    if len(recipient_ids) > 200:
        return ErrorResponse('You can generate up to 200 certificates at once.', status_code=400).to_json_response()

    issues = create_certificate_issues_bulk(
        request=request,
        certificate_type=certificate_type,
        design=design,
        recipient_ids=recipient_ids,
        issue_date=issue_date,
        custom_title=request.POST.get('custom_title', ''),
        custom_subtitle=request.POST.get('custom_subtitle', ''),
        custom_body_text=request.POST.get('custom_body_text', ''),
        custom_footer_text=request.POST.get('custom_footer_text', ''),
    )
    issue_rows = []
    for issue in issues:
        urls = build_preview_urls(issue)
        issue_rows.append({
            'id': issue.id,
            'certificateNumber': issue.certificateNumber,
            'preview_url': urls['preview_url'],
            'print_url': urls['print_url'],
            'download_url': urls['download_url'],
            'verify_url': urls['verify_url'],
        })
    return SuccessResponse(
        f'{len(issue_rows)} certificates generated successfully.',
        data={
            'issue_count': len(issue_rows),
            'issues': issue_rows,
            'preview_url': issue_rows[0]['preview_url'] if issue_rows else '',
        },
    ).to_json_response()
