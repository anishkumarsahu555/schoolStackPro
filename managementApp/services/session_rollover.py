import re
from copy import deepcopy
from datetime import date

from django.db import transaction
from django.db.models import Q

from homeApp.models import SchoolSession
from financeApp.models import (
    ExpenseCategory,
    FeeHead,
    FinanceAccount,
    FinanceApprovalRule,
    FinanceConfiguration,
    FinanceParty,
    FinancePaymentMode,
)
from hostelApp.models import (
    HostelBed,
    HostelBuilding,
    HostelFeeMapping,
    HostelFloor,
    HostelRoom,
    HostelRoomType,
)
from inventoryApp.models import InventoryCategory, InventoryItem, InventorySupplier
from libraryApp.models import (
    LibraryAuthor,
    LibraryBook,
    LibraryBookAuthor,
    LibraryCategory,
    LibraryMemberCardDesign,
    LibraryPublisher,
    LibrarySetting,
)
from managementApp.models import (
    AssignExamToClass,
    AssignSubjectsToClass,
    AssignSubjectsToTeacher,
    CoScholasticArea,
    EventType,
    Exam,
    ExamComponentType,
    ExamSubjectComponentRule,
    ExamTimeTable,
    GradingBand,
    GradingPolicy,
    LeaveType,
    Parent,
    PassPolicy,
    SchoolHoliday,
    SchoolTimetable,
    SchoolTimetableEntry,
    SchoolTimetablePeriod,
    Standard,
    Student,
    StudentIdCardDesign,
    Subjects,
    TeacherDetail,
)
from transportApp.models import (
    TransportDriver,
    TransportFeeMapping,
    TransportRoute,
    TransportStop,
    TransportVehicle,
)


DEFAULT_IMPORT_OPTIONS = {
    'copy_teachers': True,
    'copy_classes': True,
    'copy_subjects': True,
    'copy_class_subjects': True,
    'copy_teacher_subjects': True,
    'copy_exam_setup': True,
    'copy_grading_setup': True,
    'copy_leave_types': True,
    'copy_event_types': True,
    'copy_holidays': False,
    'copy_id_card_designs': False,
    'copy_timetable_setup': False,
    'copy_finance_setup': False,
    'copy_transport_setup': False,
    'copy_hostel_setup': False,
    'copy_library_setup': False,
    'copy_inventory_setup': False,
    'copy_students': False,
    'promote_students': False,
    'promotion_overrides': {},
    'selection_overrides': {},
}


IMPORTABLE_MODELS = {
    'copy_teachers': TeacherDetail,
    'copy_classes': Standard,
    'copy_subjects': Subjects,
    'copy_class_subjects': AssignSubjectsToClass,
    'copy_teacher_subjects': AssignSubjectsToTeacher,
    'copy_exam_setup': Exam,
    'copy_grading_setup': GradingPolicy,
    'copy_leave_types': LeaveType,
    'copy_event_types': EventType,
    'copy_holidays': SchoolHoliday,
    'copy_id_card_designs': StudentIdCardDesign,
    'copy_students': Student,
}


def merge_import_options(raw_options=None):
    options = deepcopy(DEFAULT_IMPORT_OPTIONS)
    if raw_options:
        for key, value in raw_options.items():
            if key in options:
                if key == 'promotion_overrides':
                    options[key] = value or {}
                elif key == 'selection_overrides':
                    options[key] = value or {}
                else:
                    options[key] = bool(value)

    if not options['copy_classes']:
        options['copy_class_subjects'] = False
    if not options['copy_classes'] or not options['copy_subjects']:
        options['copy_class_subjects'] = False
    if not options['copy_class_subjects'] or not options['copy_teachers']:
        options['copy_teacher_subjects'] = False
    if not options['copy_exam_setup']:
        options['copy_grading_setup'] = False
    if not options['copy_students']:
        options['promote_students'] = False
    return options


def preview_session_import(*, school_id, source_session_id, target_session_id, options=None):
    source_session, target_session = _validate_sessions(
        school_id=school_id,
        source_session_id=source_session_id,
        target_session_id=target_session_id,
    )
    options = merge_import_options(options)
    warnings = _validate_target_session_state(target_session, options)

    counts = {}
    details = {}
    for option_key, model in IMPORTABLE_MODELS.items():
        if not options.get(option_key):
            continue
        queryset = model.objects.filter(
            schoolID_id=school_id,
            sessionID_id=source_session.id,
            isDeleted=False,
        )
        queryset = _filter_preview_queryset(options, option_key, queryset)
        counts[option_key] = queryset.count()
        details[option_key] = _preview_rows_for_option(
            option_key,
            model.objects.filter(
                schoolID_id=school_id,
                sessionID_id=source_session.id,
                isDeleted=False,
            ),
            selected_ids=_selected_ids_for_option(options, option_key),
        )

    if options['copy_exam_setup']:
        exam_to_class_qs = AssignExamToClass.objects.filter(
            schoolID_id=school_id,
            sessionID_id=source_session.id,
            isDeleted=False,
        )
        exam_to_class_qs = _filter_preview_queryset(options, 'copy_exam_to_class', exam_to_class_qs)
        counts['copy_exam_to_class'] = exam_to_class_qs.count()
        details['copy_exam_to_class'] = _preview_rows_for_option(
            'copy_exam_to_class',
            AssignExamToClass.objects.filter(
                schoolID_id=school_id,
                sessionID_id=source_session.id,
                isDeleted=False,
            ),
            selected_ids=_selected_ids_for_option(options, 'copy_exam_to_class'),
        )

        exam_timetable_qs = ExamTimeTable.objects.filter(
            schoolID_id=school_id,
            sessionID_id=source_session.id,
            isDeleted=False,
        )
        exam_timetable_qs = _filter_preview_queryset(options, 'copy_exam_timetable', exam_timetable_qs)
        counts['copy_exam_timetable'] = exam_timetable_qs.count()
        details['copy_exam_timetable'] = _preview_rows_for_option(
            'copy_exam_timetable',
            ExamTimeTable.objects.filter(
                schoolID_id=school_id,
                sessionID_id=source_session.id,
                isDeleted=False,
            ),
            selected_ids=_selected_ids_for_option(options, 'copy_exam_timetable'),
        )

        component_type_qs = ExamComponentType.objects.filter(
            schoolID_id=school_id,
            sessionID_id=source_session.id,
            isDeleted=False,
        )
        component_type_qs = _filter_preview_queryset(options, 'copy_exam_component_types', component_type_qs)
        counts['copy_exam_component_types'] = component_type_qs.count()
        details['copy_exam_component_types'] = _preview_rows_for_option(
            'copy_exam_component_types',
            ExamComponentType.objects.filter(
                schoolID_id=school_id,
                sessionID_id=source_session.id,
                isDeleted=False,
            ),
            selected_ids=_selected_ids_for_option(options, 'copy_exam_component_types'),
        )

        component_rule_qs = ExamSubjectComponentRule.objects.filter(
            schoolID_id=school_id,
            sessionID_id=source_session.id,
            isDeleted=False,
        )
        component_rule_qs = _filter_preview_queryset(options, 'copy_component_rules', component_rule_qs)
        counts['copy_component_rules'] = component_rule_qs.count()
        details['copy_component_rules'] = _preview_rows_for_option(
            'copy_component_rules',
            ExamSubjectComponentRule.objects.filter(
                schoolID_id=school_id,
                sessionID_id=source_session.id,
                isDeleted=False,
            ),
            selected_ids=_selected_ids_for_option(options, 'copy_component_rules'),
        )

        area_qs = CoScholasticArea.objects.filter(
            schoolID_id=school_id,
            sessionID_id=source_session.id,
            isDeleted=False,
        )
        area_qs = _filter_preview_queryset(options, 'copy_co_scholastic_areas', area_qs)
        counts['copy_co_scholastic_areas'] = area_qs.count()
        details['copy_co_scholastic_areas'] = _preview_rows_for_option(
            'copy_co_scholastic_areas',
            CoScholasticArea.objects.filter(
                schoolID_id=school_id,
                sessionID_id=source_session.id,
                isDeleted=False,
            ),
            selected_ids=_selected_ids_for_option(options, 'copy_co_scholastic_areas'),
        )

        pass_policy_qs = PassPolicy.objects.filter(
            schoolID_id=school_id,
            sessionID_id=source_session.id,
            isDeleted=False,
        )
        pass_policy_qs = _filter_preview_queryset(options, 'copy_pass_policies', pass_policy_qs)
        counts['copy_pass_policies'] = pass_policy_qs.count()
        details['copy_pass_policies'] = _preview_rows_for_option(
            'copy_pass_policies',
            PassPolicy.objects.filter(
                schoolID_id=school_id,
                sessionID_id=source_session.id,
                isDeleted=False,
            ),
            selected_ids=_selected_ids_for_option(options, 'copy_pass_policies'),
        )
    if options['copy_grading_setup']:
        grading_band_qs = GradingBand.objects.filter(
            schoolID_id=school_id,
            sessionID_id=source_session.id,
            isDeleted=False,
        )
        grading_band_qs = _filter_preview_queryset(options, 'copy_grading_bands', grading_band_qs)
        counts['copy_grading_bands'] = grading_band_qs.count()
        details['copy_grading_bands'] = _preview_rows_for_option(
            'copy_grading_bands',
            GradingBand.objects.filter(
                schoolID_id=school_id,
                sessionID_id=source_session.id,
                isDeleted=False,
            ),
            selected_ids=_selected_ids_for_option(options, 'copy_grading_bands'),
        )
    if options['copy_timetable_setup']:
        timetable_qs = SchoolTimetable.objects.filter(
            schoolID_id=school_id,
            sessionID_id=source_session.id,
            isDeleted=False,
        )
        timetable_qs = _filter_preview_queryset(options, 'copy_timetable_setup', timetable_qs)
        counts['copy_timetable_setup'] = timetable_qs.count()
        details['copy_timetable_setup'] = _preview_rows_for_option(
            'copy_timetable_setup',
            SchoolTimetable.objects.filter(
                schoolID_id=school_id,
                sessionID_id=source_session.id,
                isDeleted=False,
            ),
            selected_ids=_selected_ids_for_option(options, 'copy_timetable_setup'),
        )

        period_qs = SchoolTimetablePeriod.objects.filter(
            schoolID_id=school_id,
            sessionID_id=source_session.id,
            isDeleted=False,
        )
        period_qs = _filter_preview_queryset(options, 'copy_timetable_periods', period_qs)
        counts['copy_timetable_periods'] = period_qs.count()
        details['copy_timetable_periods'] = _preview_rows_for_option(
            'copy_timetable_periods',
            SchoolTimetablePeriod.objects.filter(
                schoolID_id=school_id,
                sessionID_id=source_session.id,
                isDeleted=False,
            ),
            selected_ids=_selected_ids_for_option(options, 'copy_timetable_periods'),
        )

        entry_qs = SchoolTimetableEntry.objects.filter(
            schoolID_id=school_id,
            sessionID_id=source_session.id,
            isDeleted=False,
        )
        entry_qs = _filter_preview_queryset(options, 'copy_timetable_entries', entry_qs)
        counts['copy_timetable_entries'] = entry_qs.count()
        details['copy_timetable_entries'] = _preview_rows_for_option(
            'copy_timetable_entries',
            SchoolTimetableEntry.objects.filter(
                schoolID_id=school_id,
                sessionID_id=source_session.id,
                isDeleted=False,
            ),
            selected_ids=_selected_ids_for_option(options, 'copy_timetable_entries'),
        )
    if options['copy_finance_setup']:
        _add_preview_group(counts, details, school_id, source_session.id, options, [
            ('copy_finance_accounts', FinanceAccount),
            ('copy_finance_parties', FinanceParty),
            ('copy_finance_payment_modes', FinancePaymentMode),
            ('copy_finance_configuration', FinanceConfiguration),
            ('copy_fee_heads', FeeHead),
            ('copy_expense_categories', ExpenseCategory),
            ('copy_finance_approval_rules', FinanceApprovalRule),
        ])
    if options['copy_transport_setup']:
        _add_preview_group(counts, details, school_id, source_session.id, options, [
            ('copy_transport_routes', TransportRoute),
            ('copy_transport_stops', TransportStop),
            ('copy_transport_drivers', TransportDriver),
            ('copy_transport_vehicles', TransportVehicle),
            ('copy_transport_fee_mappings', TransportFeeMapping),
        ])
    if options['copy_hostel_setup']:
        _add_preview_group(counts, details, school_id, source_session.id, options, [
            ('copy_hostel_buildings', HostelBuilding),
            ('copy_hostel_floors', HostelFloor),
            ('copy_hostel_room_types', HostelRoomType),
            ('copy_hostel_rooms', HostelRoom),
            ('copy_hostel_beds', HostelBed),
            ('copy_hostel_fee_mappings', HostelFeeMapping),
        ])
    if options['copy_library_setup']:
        _add_preview_group(counts, details, school_id, source_session.id, options, [
            ('copy_library_categories', LibraryCategory),
            ('copy_library_authors', LibraryAuthor),
            ('copy_library_publishers', LibraryPublisher),
            ('copy_library_books', LibraryBook),
            ('copy_library_card_designs', LibraryMemberCardDesign),
            ('copy_library_settings', LibrarySetting),
        ])
    if options['copy_inventory_setup']:
        _add_preview_group(counts, details, school_id, source_session.id, options, [
            ('copy_inventory_categories', InventoryCategory),
            ('copy_inventory_suppliers', InventorySupplier),
            ('copy_inventory_items', InventoryItem),
        ])
    if options['copy_students']:
        parent_qs = Parent.objects.filter(
            schoolID_id=school_id,
            sessionID_id=source_session.id,
            isDeleted=False,
        )
        parent_qs = _filter_preview_queryset(options, 'copy_parents', parent_qs)
        counts['copy_parents'] = parent_qs.count()
        details['copy_parents'] = _preview_rows_for_option(
            'copy_parents',
            Parent.objects.filter(
                schoolID_id=school_id,
                sessionID_id=source_session.id,
                isDeleted=False,
            ),
            selected_ids=_selected_ids_for_option(options, 'copy_parents'),
        )
        if options['promote_students']:
            promotion_preview = _build_promotion_preview(
                source_session.id,
                target_session.id,
                options.get('promotion_overrides'),
            )
            warnings.extend(promotion_preview['warnings'])
        else:
            promotion_preview = {'rows': []}
    else:
        promotion_preview = {'rows': []}

    return {
        'sourceSession': _serialize_session(source_session),
        'targetSession': _serialize_session(target_session),
        'options': options,
        'counts': counts,
        'details': details,
        'warnings': _dedupe_list(warnings),
        'promotionPreview': promotion_preview['rows'][:20],
    }


def _add_preview_group(counts, details, school_id, source_session_id, options, specs):
    for option_key, model in specs:
        queryset = model.objects.filter(
            schoolID_id=school_id,
            sessionID_id=source_session_id,
            isDeleted=False,
        )
        queryset = _filter_preview_queryset(options, option_key, queryset)
        counts[option_key] = queryset.count()
        details[option_key] = _preview_rows_for_option(
            option_key,
            model.objects.filter(
                schoolID_id=school_id,
                sessionID_id=source_session_id,
                isDeleted=False,
            ),
            selected_ids=_selected_ids_for_option(options, option_key),
        )


@transaction.atomic
def run_session_import(*, school_id, source_session_id, target_session_id, options=None, acting_user=None):
    source_session, target_session = _validate_sessions(
        school_id=school_id,
        source_session_id=source_session_id,
        target_session_id=target_session_id,
    )
    options = merge_import_options(options)
    warnings = _validate_target_session_state(target_session, options)

    service = SessionImportService(
        school_id=school_id,
        source_session=source_session,
        target_session=target_session,
        options=options,
        acting_user=acting_user,
    )
    result = service.run()
    result['warnings'] = _dedupe_list(warnings + result.get('warnings', []))
    return result


class SessionImportService:
    def __init__(self, *, school_id, source_session, target_session, options, acting_user=None):
        self.school_id = school_id
        self.source_session = source_session
        self.target_session = target_session
        self.options = options
        self.acting_user = acting_user

        self.summary = {
            'copied': {},
            'skipped': {},
            'warnings': [],
        }
        self.teacher_map = {}
        self.standard_map = {}
        self.subject_map = {}
        self.assign_subject_map = {}
        self.exam_map = {}
        self.assign_exam_map = {}
        self.component_type_map = {}
        self.grading_policy_map = {}
        self.component_rule_map = {}
        self.area_map = {}
        self.parent_map = {}
        self.student_map = {}
        self.timetable_map = {}
        self.timetable_period_map = {}
        self.finance_account_map = {}
        self.finance_party_map = {}
        self.transport_route_map = {}
        self.transport_stop_map = {}
        self.transport_driver_map = {}
        self.transport_vehicle_map = {}
        self.hostel_building_map = {}
        self.hostel_floor_map = {}
        self.hostel_room_type_map = {}
        self.hostel_room_map = {}
        self.hostel_bed_map = {}
        self.library_category_map = {}
        self.library_author_map = {}
        self.library_publisher_map = {}
        self.library_book_map = {}
        self.inventory_category_map = {}
        self.inventory_supplier_map = {}
        self.inventory_item_map = {}

    def _selected_ids(self, option_key):
        return _selected_ids_for_option(self.options, option_key)

    def _filter_queryset(self, option_key, queryset):
        selected_ids = self._selected_ids(option_key)
        if selected_ids:
            return queryset.filter(id__in=selected_ids)
        return queryset

    def run(self):
        if self.options['copy_teachers']:
            self._copy_teachers()
        if self.options['copy_classes']:
            self._copy_classes()
        if self.options['copy_subjects']:
            self._copy_subjects()
        if self.options['copy_class_subjects']:
            self._copy_assign_subjects_to_class()
        if self.options['copy_teacher_subjects']:
            self._copy_assign_subjects_to_teacher()
        if self.options['copy_exam_setup']:
            self._copy_exams()
            self._copy_assign_exams_to_class()
            self._copy_exam_timetable()
            self._copy_component_types()
            self._copy_co_scholastic_areas()
        if self.options['copy_grading_setup']:
            self._copy_grading_policies()
            self._copy_grading_bands()
            self._copy_pass_policies()
            self._copy_component_rules()
        if self.options['copy_leave_types']:
            self._copy_leave_types()
        if self.options['copy_event_types']:
            self._copy_event_types()
        if self.options['copy_holidays']:
            self._copy_holidays()
        if self.options['copy_id_card_designs']:
            self._copy_id_card_designs()
        if self.options['copy_timetable_setup']:
            self._copy_timetables()
            self._copy_timetable_periods()
            self._copy_timetable_entries()
        if self.options['copy_students']:
            self._copy_parents()
            self._copy_students()
        if self.options['copy_finance_setup']:
            self._copy_finance_setup()
        if self.options['copy_transport_setup']:
            self._copy_transport_setup()
        if self.options['copy_hostel_setup']:
            self._copy_hostel_setup()
        if self.options['copy_library_setup']:
            self._copy_library_setup()
        if self.options['copy_inventory_setup']:
            self._copy_inventory_setup()

        return {
            'sourceSession': _serialize_session(self.source_session),
            'targetSession': _serialize_session(self.target_session),
            'options': self.options,
            'copied': self.summary['copied'],
            'skipped': self.summary['skipped'],
            'warnings': _dedupe_list(self.summary['warnings']),
        }

    def _copy_teachers(self):
        queryset = TeacherDetail.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).order_by('id')
        queryset = self._filter_queryset('copy_teachers', queryset)
        for teacher in queryset:
            existing = TeacherDetail.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                isDeleted=False,
            )
            if teacher.userID_id:
                existing = existing.filter(userID_id=teacher.userID_id)
            elif teacher.employeeCode:
                existing = existing.filter(_text_match_q('employeeCode', teacher.employeeCode))
            else:
                existing = existing.filter(_text_match_q('name', teacher.name)).filter(_text_match_q('phoneNumber', teacher.phoneNumber))
            target = existing.order_by('id').first()
            if target:
                self.teacher_map[teacher.id] = target.id
                self._mark_skipped('teachers')
                continue

            payload = _clone_payload(
                teacher,
                exclude={'id', 'sessionID', 'schoolID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={'sessionID': self.target_session, 'schoolID_id': self.school_id},
            )
            target = TeacherDetail.objects.create(**payload)
            self._touch_audit_fields(target)
            self.teacher_map[teacher.id] = target.id
            self._mark_copied('teachers')

    def _copy_classes(self):
        queryset = Standard.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).select_related('classTeacher').order_by('id')
        queryset = self._filter_queryset('copy_classes', queryset)
        for standard in queryset:
            teacher_id = self.teacher_map.get(standard.classTeacher_id)
            existing = Standard.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                isDeleted=False,
            ).filter(
                _text_match_q('name', standard.name)
            ).filter(
                _text_match_q('section', standard.section)
            ).order_by('id').first()
            if existing:
                self.standard_map[standard.id] = existing.id
                self._mark_skipped('classes')
                continue

            payload = _clone_payload(
                standard,
                exclude={'id', 'sessionID', 'schoolID', 'classTeacher', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={
                    'sessionID': self.target_session,
                    'schoolID_id': self.school_id,
                    'classTeacher_id': teacher_id,
                },
            )
            target = Standard.objects.create(**payload)
            self._touch_audit_fields(target)
            self.standard_map[standard.id] = target.id
            self._mark_copied('classes')

    def _copy_subjects(self):
        queryset = Subjects.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).order_by('id')
        queryset = self._filter_queryset('copy_subjects', queryset)
        for subject in queryset:
            existing = Subjects.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                isDeleted=False,
            ).filter(_text_match_q('name', subject.name)).order_by('id').first()
            if existing:
                self.subject_map[subject.id] = existing.id
                self._mark_skipped('subjects')
                continue

            payload = _clone_payload(
                subject,
                exclude={'id', 'sessionID', 'schoolID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={'sessionID': self.target_session, 'schoolID_id': self.school_id},
            )
            target = Subjects.objects.create(**payload)
            self._touch_audit_fields(target)
            self.subject_map[subject.id] = target.id
            self._mark_copied('subjects')

    def _copy_assign_subjects_to_class(self):
        queryset = AssignSubjectsToClass.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).order_by('id')
        queryset = self._filter_queryset('copy_class_subjects', queryset)
        for row in queryset:
            standard_id = self.standard_map.get(row.standardID_id)
            subject_id = self.subject_map.get(row.subjectID_id)
            if not standard_id or not subject_id:
                self.summary['warnings'].append('Some class-subject mappings were skipped because matching class or subject was unavailable in the target session.')
                self._mark_skipped('classSubjects')
                continue

            existing = AssignSubjectsToClass.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                isDeleted=False,
                standardID_id=standard_id,
                subjectID_id=subject_id,
            ).order_by('id').first()
            if existing:
                self.assign_subject_map[row.id] = existing.id
                self._mark_skipped('classSubjects')
                continue

            payload = _clone_payload(
                row,
                exclude={'id', 'sessionID', 'schoolID', 'standardID', 'subjectID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={
                    'sessionID': self.target_session,
                    'schoolID_id': self.school_id,
                    'standardID_id': standard_id,
                    'subjectID_id': subject_id,
                },
            )
            target = AssignSubjectsToClass.objects.create(**payload)
            self._touch_audit_fields(target)
            self.assign_subject_map[row.id] = target.id
            self._mark_copied('classSubjects')

    def _copy_assign_subjects_to_teacher(self):
        queryset = AssignSubjectsToTeacher.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).order_by('id')
        queryset = self._filter_queryset('copy_teacher_subjects', queryset)
        for row in queryset:
            assigned_subject_id = self.assign_subject_map.get(row.assignedSubjectID_id)
            teacher_id = self.teacher_map.get(row.teacherID_id)
            if not assigned_subject_id or not teacher_id:
                self.summary['warnings'].append('Some teacher subject assignments were skipped because the linked teacher or subject mapping was unavailable in the target session.')
                self._mark_skipped('teacherSubjects')
                continue

            existing = AssignSubjectsToTeacher.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                isDeleted=False,
                assignedSubjectID_id=assigned_subject_id,
                teacherID_id=teacher_id,
                subjectBranch=(row.subjectBranch or ''),
            ).order_by('id').first()
            if existing:
                self._mark_skipped('teacherSubjects')
                continue

            payload = _clone_payload(
                row,
                exclude={'id', 'sessionID', 'schoolID', 'assignedSubjectID', 'teacherID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={
                    'sessionID': self.target_session,
                    'schoolID_id': self.school_id,
                    'assignedSubjectID_id': assigned_subject_id,
                    'teacherID_id': teacher_id,
                },
            )
            target = AssignSubjectsToTeacher.objects.create(**payload)
            self._touch_audit_fields(target)
            self._mark_copied('teacherSubjects')

    def _copy_exams(self):
        queryset = Exam.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).order_by('id')
        queryset = self._filter_queryset('copy_exam_setup', queryset)
        for row in queryset:
            existing = Exam.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                isDeleted=False,
            ).filter(_text_match_q('name', row.name)).order_by('id').first()
            if existing:
                self.exam_map[row.id] = existing.id
                self._mark_skipped('exams')
                continue

            payload = _clone_payload(
                row,
                exclude={'id', 'sessionID', 'schoolID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={'sessionID': self.target_session, 'schoolID_id': self.school_id},
            )
            target = Exam.objects.create(**payload)
            self._touch_audit_fields(target)
            self.exam_map[row.id] = target.id
            self._mark_copied('exams')

    def _copy_assign_exams_to_class(self):
        queryset = AssignExamToClass.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).order_by('id')
        queryset = self._filter_queryset('copy_exam_to_class', queryset)
        for row in queryset:
            standard_id = self.standard_map.get(row.standardID_id)
            exam_id = self.exam_map.get(row.examID_id)
            if not standard_id or not exam_id:
                self.summary['warnings'].append('Some exam-class mappings were skipped because the linked class or exam was unavailable in the target session.')
                self._mark_skipped('examClasses')
                continue

            existing = AssignExamToClass.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                isDeleted=False,
                standardID_id=standard_id,
                examID_id=exam_id,
            ).order_by('id').first()
            if existing:
                self.assign_exam_map[row.id] = existing.id
                self._mark_skipped('examClasses')
                continue

            payload = _clone_payload(
                row,
                exclude={'id', 'sessionID', 'schoolID', 'standardID', 'examID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={
                    'sessionID': self.target_session,
                    'schoolID_id': self.school_id,
                    'standardID_id': standard_id,
                    'examID_id': exam_id,
                },
            )
            target = AssignExamToClass.objects.create(**payload)
            self._touch_audit_fields(target)
            self.assign_exam_map[row.id] = target.id
            self._mark_copied('examClasses')

    def _copy_exam_timetable(self):
        queryset = ExamTimeTable.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).order_by('id')
        queryset = self._filter_queryset('copy_exam_timetable', queryset)
        for row in queryset:
            standard_id = self.standard_map.get(row.standardID_id)
            exam_id = self.exam_map.get(row.examID_id)
            subject_id = self.subject_map.get(row.subjectID_id)
            if not standard_id or not exam_id or not subject_id:
                self.summary['warnings'].append('Some exam timetable rows were skipped because the linked class, exam, or subject was unavailable in the target session.')
                self._mark_skipped('examTimetable')
                continue

            existing = ExamTimeTable.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                isDeleted=False,
                standardID_id=standard_id,
                examID_id=exam_id,
                subjectID_id=subject_id,
                examDate=row.examDate,
            ).order_by('id').first()
            if existing:
                self._mark_skipped('examTimetable')
                continue

            payload = _clone_payload(
                row,
                exclude={'id', 'sessionID', 'schoolID', 'standardID', 'examID', 'subjectID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={
                    'sessionID': self.target_session,
                    'schoolID_id': self.school_id,
                    'standardID_id': standard_id,
                    'examID_id': exam_id,
                    'subjectID_id': subject_id,
                },
            )
            target = ExamTimeTable.objects.create(**payload)
            self._touch_audit_fields(target)
            self._mark_copied('examTimetable')

    def _copy_component_types(self):
        queryset = ExamComponentType.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).order_by('id')
        queryset = self._filter_queryset('copy_exam_component_types', queryset)
        for row in queryset:
            existing = ExamComponentType.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                isDeleted=False,
            ).filter(_text_match_q('code', row.code)).order_by('id').first()
            if not existing and row.name:
                existing = ExamComponentType.objects.filter(
                    schoolID_id=self.school_id,
                    sessionID_id=self.target_session.id,
                    isDeleted=False,
                ).filter(_text_match_q('name', row.name)).order_by('id').first()
            if existing:
                self.component_type_map[row.id] = existing.id
                self._mark_skipped('examComponentTypes')
                continue

            payload = _clone_payload(
                row,
                exclude={'id', 'sessionID', 'schoolID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={'sessionID': self.target_session, 'schoolID_id': self.school_id},
            )
            target = ExamComponentType.objects.create(**payload)
            self._touch_audit_fields(target)
            self.component_type_map[row.id] = target.id
            self._mark_copied('examComponentTypes')

    def _copy_grading_policies(self):
        queryset = GradingPolicy.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).order_by('id')
        queryset = self._filter_queryset('copy_grading_setup', queryset)
        for row in queryset:
            existing = GradingPolicy.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                isDeleted=False,
            ).filter(_text_match_q('name', row.name)).order_by('id').first()
            if existing:
                self.grading_policy_map[row.id] = existing.id
                self._mark_skipped('gradingPolicies')
                continue

            payload = _clone_payload(
                row,
                exclude={'id', 'sessionID', 'schoolID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={'sessionID': self.target_session, 'schoolID_id': self.school_id},
            )
            target = GradingPolicy.objects.create(**payload)
            self._touch_audit_fields(target)
            self.grading_policy_map[row.id] = target.id
            self._mark_copied('gradingPolicies')

    def _copy_grading_bands(self):
        queryset = GradingBand.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).order_by('id')
        queryset = self._filter_queryset('copy_grading_bands', queryset)
        for row in queryset:
            policy_id = self.grading_policy_map.get(row.policyID_id)
            if not policy_id:
                self.summary['warnings'].append('Some grading bands were skipped because their grading policy was unavailable in the target session.')
                self._mark_skipped('gradingBands')
                continue

            existing = GradingBand.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                policyID_id=policy_id,
                isDeleted=False,
                gradeLabel=row.gradeLabel,
            ).order_by('id').first()
            if existing:
                self._mark_skipped('gradingBands')
                continue

            payload = _clone_payload(
                row,
                exclude={'id', 'policyID', 'sessionID', 'schoolID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={'policyID_id': policy_id, 'sessionID': self.target_session, 'schoolID_id': self.school_id},
            )
            target = GradingBand.objects.create(**payload)
            self._touch_audit_fields(target)
            self._mark_copied('gradingBands')

    def _copy_pass_policies(self):
        queryset = PassPolicy.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).order_by('id')
        queryset = self._filter_queryset('copy_pass_policies', queryset)
        for row in queryset:
            exam_id = self.assign_exam_map.get(row.examID_id)
            grading_policy_id = self.grading_policy_map.get(row.gradingPolicyID_id)
            if not exam_id:
                self.summary['warnings'].append('Some pass policies were skipped because their linked exam assignment was unavailable in the target session.')
                self._mark_skipped('passPolicies')
                continue

            existing = PassPolicy.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                examID_id=exam_id,
                isDeleted=False,
            ).order_by('id').first()
            if existing:
                self._mark_skipped('passPolicies')
                continue

            payload = _clone_payload(
                row,
                exclude={'id', 'sessionID', 'schoolID', 'examID', 'gradingPolicyID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={
                    'sessionID': self.target_session,
                    'schoolID_id': self.school_id,
                    'examID_id': exam_id,
                    'gradingPolicyID_id': grading_policy_id,
                },
            )
            target = PassPolicy.objects.create(**payload)
            self._touch_audit_fields(target)
            self._mark_copied('passPolicies')

    def _copy_component_rules(self):
        queryset = ExamSubjectComponentRule.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).order_by('id')
        queryset = self._filter_queryset('copy_component_rules', queryset)
        for row in queryset:
            exam_id = self.assign_exam_map.get(row.examID_id)
            subject_id = self.assign_subject_map.get(row.subjectID_id)
            component_type_id = self.component_type_map.get(row.componentTypeID_id)
            if not exam_id or not subject_id or not component_type_id:
                self.summary['warnings'].append('Some exam component rules were skipped because linked exam, subject, or component type was unavailable in the target session.')
                self._mark_skipped('componentRules')
                continue

            existing = ExamSubjectComponentRule.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                examID_id=exam_id,
                subjectID_id=subject_id,
                componentTypeID_id=component_type_id,
                isDeleted=False,
            ).order_by('id').first()
            if existing:
                self.component_rule_map[row.id] = existing.id
                self._mark_skipped('componentRules')
                continue

            payload = _clone_payload(
                row,
                exclude={'id', 'sessionID', 'schoolID', 'examID', 'subjectID', 'componentTypeID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={
                    'sessionID': self.target_session,
                    'schoolID_id': self.school_id,
                    'examID_id': exam_id,
                    'subjectID_id': subject_id,
                    'componentTypeID_id': component_type_id,
                },
            )
            target = ExamSubjectComponentRule.objects.create(**payload)
            self._touch_audit_fields(target)
            self.component_rule_map[row.id] = target.id
            self._mark_copied('componentRules')

    def _copy_co_scholastic_areas(self):
        queryset = CoScholasticArea.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).order_by('id')
        queryset = self._filter_queryset('copy_co_scholastic_areas', queryset)
        for row in queryset:
            existing = CoScholasticArea.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                isDeleted=False,
            ).filter(_text_match_q('code', row.code)).order_by('id').first()
            if not existing and row.name:
                existing = CoScholasticArea.objects.filter(
                    schoolID_id=self.school_id,
                    sessionID_id=self.target_session.id,
                    isDeleted=False,
                ).filter(_text_match_q('name', row.name)).order_by('id').first()
            if existing:
                self.area_map[row.id] = existing.id
                self._mark_skipped('coScholasticAreas')
                continue

            payload = _clone_payload(
                row,
                exclude={'id', 'sessionID', 'schoolID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={'sessionID': self.target_session, 'schoolID_id': self.school_id},
            )
            target = CoScholasticArea.objects.create(**payload)
            self._touch_audit_fields(target)
            self.area_map[row.id] = target.id
            self._mark_copied('coScholasticAreas')

    def _copy_leave_types(self):
        queryset = LeaveType.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).order_by('id')
        queryset = self._filter_queryset('copy_leave_types', queryset)
        for row in queryset:
            existing = LeaveType.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                isDeleted=False,
            ).filter(_text_match_q('name', row.name)).order_by('id').first()
            if existing:
                self._mark_skipped('leaveTypes')
                continue

            payload = _clone_payload(
                row,
                exclude={'id', 'sessionID', 'schoolID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={'sessionID': self.target_session, 'schoolID_id': self.school_id},
            )
            target = LeaveType.objects.create(**payload)
            self._touch_audit_fields(target)
            self._mark_copied('leaveTypes')

    def _copy_event_types(self):
        queryset = EventType.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).order_by('id')
        queryset = self._filter_queryset('copy_event_types', queryset)
        for row in queryset:
            existing = EventType.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                isDeleted=False,
            ).filter(_text_match_q('name', row.name)).filter(_text_match_q('audience', row.audience)).order_by('id').first()
            if existing:
                self._mark_skipped('eventTypes')
                continue

            payload = _clone_payload(
                row,
                exclude={'id', 'sessionID', 'schoolID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={'sessionID': self.target_session, 'schoolID_id': self.school_id},
            )
            target = EventType.objects.create(**payload)
            self._touch_audit_fields(target)
            self._mark_copied('eventTypes')

    def _copy_holidays(self):
        queryset = SchoolHoliday.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).order_by('startDate', 'id')
        queryset = self._filter_queryset('copy_holidays', queryset)
        for row in queryset:
            existing = SchoolHoliday.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                isDeleted=False,
                startDate=row.startDate,
                endDate=row.endDate,
                holidayType=row.holidayType,
                appliesTo=row.appliesTo,
            ).filter(_text_match_q('title', row.title)).order_by('id').first()
            if existing:
                self._mark_skipped('holidays')
                continue

            payload = _clone_payload(
                row,
                exclude={'id', 'sessionID', 'schoolID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={'sessionID': self.target_session, 'schoolID_id': self.school_id},
            )
            target = SchoolHoliday.objects.create(**payload)
            self._touch_audit_fields(target)
            self._mark_copied('holidays')

    def _copy_id_card_designs(self):
        queryset = StudentIdCardDesign.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).order_by('name', 'id')
        queryset = self._filter_queryset('copy_id_card_designs', queryset)
        for row in queryset:
            existing = StudentIdCardDesign.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                isDeleted=False,
            ).filter(_text_match_q('name', row.name)).order_by('id').first()
            if existing:
                self._mark_skipped('idCardDesigns')
                continue

            payload = _clone_payload(
                row,
                exclude={'id', 'sessionID', 'schoolID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={'sessionID': self.target_session, 'schoolID_id': self.school_id},
            )
            target = StudentIdCardDesign.objects.create(**payload)
            self._touch_audit_fields(target)
            self._mark_copied('idCardDesigns')

    def _copy_timetables(self):
        self._ensure_academic_maps_for_timetable()
        queryset = SchoolTimetable.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).select_related('standardID').order_by('id')
        queryset = self._filter_queryset('copy_timetable_setup', queryset)
        for row in queryset:
            standard_id = self.standard_map.get(row.standardID_id)
            if row.standardID_id and not standard_id:
                self.summary['warnings'].append('Some timetables were skipped because matching target classes were unavailable.')
                self._mark_skipped('timetables')
                continue

            existing = SchoolTimetable.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                standardID_id=standard_id,
                isDeleted=False,
            ).filter(_text_match_q('name', row.name)).order_by('id').first()
            if existing:
                self.timetable_map[row.id] = existing.id
                self._mark_skipped('timetables')
                continue

            payload = _clone_payload(
                row,
                exclude={'id', 'sessionID', 'schoolID', 'standardID', 'publishedByUserID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={
                    'sessionID': self.target_session,
                    'schoolID_id': self.school_id,
                    'standardID_id': standard_id,
                    'publishedByUserID_id': None,
                    'publishedOn': None,
                    'status': 'draft',
                },
            )
            target = SchoolTimetable.objects.create(**payload)
            self._touch_audit_fields(target)
            self.timetable_map[row.id] = target.id
            self._mark_copied('timetables')

    def _copy_timetable_periods(self):
        queryset = SchoolTimetablePeriod.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).order_by('timetableID_id', 'displayOrder', 'id')
        queryset = self._filter_queryset('copy_timetable_periods', queryset)
        for row in queryset:
            timetable_id = self.timetable_map.get(row.timetableID_id)
            if not timetable_id:
                self.summary['warnings'].append('Some timetable periods were skipped because their timetable was unavailable in the target session.')
                self._mark_skipped('timetablePeriods')
                continue

            existing = SchoolTimetablePeriod.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                timetableID_id=timetable_id,
                isDeleted=False,
                startTime=row.startTime,
                endTime=row.endTime,
                displayOrder=row.displayOrder,
            ).filter(_text_match_q('name', row.name)).order_by('id').first()
            if existing:
                self.timetable_period_map[row.id] = existing.id
                self._mark_skipped('timetablePeriods')
                continue

            payload = _clone_payload(
                row,
                exclude={'id', 'sessionID', 'schoolID', 'timetableID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={
                    'sessionID': self.target_session,
                    'schoolID_id': self.school_id,
                    'timetableID_id': timetable_id,
                },
            )
            target = SchoolTimetablePeriod.objects.create(**payload)
            self._touch_audit_fields(target)
            self.timetable_period_map[row.id] = target.id
            self._mark_copied('timetablePeriods')

    def _copy_timetable_entries(self):
        queryset = SchoolTimetableEntry.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).order_by('timetableID_id', 'dayOfWeek', 'periodID_id', 'id')
        queryset = self._filter_queryset('copy_timetable_entries', queryset)
        for row in queryset:
            timetable_id = self.timetable_map.get(row.timetableID_id)
            period_id = self.timetable_period_map.get(row.periodID_id)
            assigned_subject_id = self.assign_subject_map.get(row.assignedSubjectID_id)
            teacher_id = self.teacher_map.get(row.teacherID_id)
            if not timetable_id or not period_id:
                self.summary['warnings'].append('Some timetable entries were skipped because their timetable or period was unavailable in the target session.')
                self._mark_skipped('timetableEntries')
                continue
            if row.assignedSubjectID_id and not assigned_subject_id:
                self.summary['warnings'].append('Some timetable entries were skipped because their class-subject mapping was unavailable in the target session.')
                self._mark_skipped('timetableEntries')
                continue
            if row.teacherID_id and not teacher_id:
                self.summary['warnings'].append('Some timetable entries were skipped because their teacher was unavailable in the target session.')
                self._mark_skipped('timetableEntries')
                continue

            existing = SchoolTimetableEntry.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                timetableID_id=timetable_id,
                periodID_id=period_id,
                dayOfWeek=row.dayOfWeek,
                isDeleted=False,
            ).order_by('id').first()
            if existing:
                self._mark_skipped('timetableEntries')
                continue

            payload = _clone_payload(
                row,
                exclude={'id', 'sessionID', 'schoolID', 'timetableID', 'periodID', 'assignedSubjectID', 'teacherID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={
                    'sessionID': self.target_session,
                    'schoolID_id': self.school_id,
                    'timetableID_id': timetable_id,
                    'periodID_id': period_id,
                    'assignedSubjectID_id': assigned_subject_id,
                    'teacherID_id': teacher_id,
                },
            )
            target = SchoolTimetableEntry.objects.create(**payload)
            self._touch_audit_fields(target)
            self._mark_copied('timetableEntries')

    def _ensure_academic_maps_for_timetable(self):
        if not self.standard_map:
            for row in Standard.objects.filter(schoolID_id=self.school_id, sessionID_id=self.source_session.id, isDeleted=False):
                target = Standard.objects.filter(
                    schoolID_id=self.school_id,
                    sessionID_id=self.target_session.id,
                    isDeleted=False,
                ).filter(_text_match_q('name', row.name)).filter(_text_match_q('section', row.section)).order_by('id').first()
                if target:
                    self.standard_map[row.id] = target.id
        if not self.subject_map:
            for row in Subjects.objects.filter(schoolID_id=self.school_id, sessionID_id=self.source_session.id, isDeleted=False):
                target = Subjects.objects.filter(
                    schoolID_id=self.school_id,
                    sessionID_id=self.target_session.id,
                    isDeleted=False,
                ).filter(_text_match_q('name', row.name)).order_by('id').first()
                if target:
                    self.subject_map[row.id] = target.id
        if not self.teacher_map:
            for row in TeacherDetail.objects.filter(schoolID_id=self.school_id, sessionID_id=self.source_session.id, isDeleted=False):
                target_qs = TeacherDetail.objects.filter(
                    schoolID_id=self.school_id,
                    sessionID_id=self.target_session.id,
                    isDeleted=False,
                )
                if row.userID_id:
                    target_qs = target_qs.filter(userID_id=row.userID_id)
                elif row.employeeCode:
                    target_qs = target_qs.filter(_text_match_q('employeeCode', row.employeeCode))
                else:
                    target_qs = target_qs.filter(_text_match_q('name', row.name)).filter(_text_match_q('phoneNumber', row.phoneNumber))
                target = target_qs.order_by('id').first()
                if target:
                    self.teacher_map[row.id] = target.id
        if not self.assign_subject_map:
            for row in AssignSubjectsToClass.objects.filter(schoolID_id=self.school_id, sessionID_id=self.source_session.id, isDeleted=False):
                standard_id = self.standard_map.get(row.standardID_id)
                subject_id = self.subject_map.get(row.subjectID_id)
                if not standard_id or not subject_id:
                    continue
                target = AssignSubjectsToClass.objects.filter(
                    schoolID_id=self.school_id,
                    sessionID_id=self.target_session.id,
                    standardID_id=standard_id,
                    subjectID_id=subject_id,
                    isDeleted=False,
                ).order_by('id').first()
                if target:
                    self.assign_subject_map[row.id] = target.id

    def _copy_finance_setup(self):
        self._ensure_profile_maps_for_cross_module_setup()
        self._copy_finance_accounts()
        self._copy_finance_parties()
        self._copy_finance_payment_modes()
        self._copy_finance_configuration()
        self._copy_fee_heads()
        self._copy_expense_categories()
        self._copy_finance_approval_rules()

    def _copy_finance_accounts(self):
        queryset = FinanceAccount.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).order_by('parentAccountID_id', 'accountCode', 'id')
        queryset = self._filter_queryset('copy_finance_accounts', queryset)
        source_rows = list(queryset)
        deferred_parents = []
        for row in source_rows:
            existing = FinanceAccount.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                isDeleted=False,
            ).filter(_text_match_q('accountCode', row.accountCode)).order_by('id').first()
            if existing:
                self.finance_account_map[row.id] = existing.id
                self._mark_skipped('financeAccounts')
                continue
            payload = _clone_payload(
                row,
                exclude={'id', 'sessionID', 'schoolID', 'parentAccountID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={
                    'sessionID': self.target_session,
                    'schoolID_id': self.school_id,
                    'parentAccountID_id': None,
                    'openingBalance': 0,
                },
            )
            target = FinanceAccount.objects.create(**payload)
            self._touch_audit_fields(target)
            self.finance_account_map[row.id] = target.id
            deferred_parents.append((target, row.parentAccountID_id))
            self._mark_copied('financeAccounts')
        for target, source_parent_id in deferred_parents:
            parent_id = self.finance_account_map.get(source_parent_id)
            if parent_id:
                target.parentAccountID_id = parent_id
                target.save(update_fields=['parentAccountID', 'lastUpdatedOn'])

    def _copy_finance_parties(self):
        queryset = FinanceParty.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).order_by('partyType', 'displayName', 'id')
        queryset = self._filter_queryset('copy_finance_parties', queryset)
        for row in queryset:
            student_id = self.student_map.get(row.studentID_id)
            parent_id = self.parent_map.get(row.parentID_id)
            teacher_id = self.teacher_map.get(row.teacherID_id)
            if row.studentID_id and not student_id:
                self._mark_skipped('financeParties')
                continue
            if row.parentID_id and not parent_id:
                self._mark_skipped('financeParties')
                continue
            if row.teacherID_id and not teacher_id:
                self._mark_skipped('financeParties')
                continue
            existing = FinanceParty.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                partyType=row.partyType,
                isDeleted=False,
            ).filter(_text_match_q('displayName', row.displayName)).filter(_text_match_q('phoneNumber', row.phoneNumber)).order_by('id').first()
            if existing:
                self.finance_party_map[row.id] = existing.id
                self._mark_skipped('financeParties')
                continue
            payload = _clone_payload(
                row,
                exclude={'id', 'sessionID', 'schoolID', 'studentID', 'parentID', 'teacherID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={
                    'sessionID': self.target_session,
                    'schoolID_id': self.school_id,
                    'studentID_id': student_id,
                    'parentID_id': parent_id,
                    'teacherID_id': teacher_id,
                },
            )
            target = FinanceParty.objects.create(**payload)
            self._touch_audit_fields(target)
            self.finance_party_map[row.id] = target.id
            self._mark_copied('financeParties')

    def _copy_finance_payment_modes(self):
        queryset = FinancePaymentMode.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).order_by('name', 'id')
        queryset = self._filter_queryset('copy_finance_payment_modes', queryset)
        for row in queryset:
            linked_account_id = self.finance_account_map.get(row.linkedAccountID_id)
            existing = FinancePaymentMode.objects.filter(
                schoolID_id=self.school_id,
                isDeleted=False,
            ).filter(_text_match_q('code', row.code)).order_by('id').first()
            if existing:
                self._mark_skipped('financePaymentModes')
                continue
            payload = _clone_payload(
                row,
                exclude={'id', 'sessionID', 'schoolID', 'linkedAccountID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={
                    'sessionID': self.target_session,
                    'schoolID_id': self.school_id,
                    'linkedAccountID_id': linked_account_id,
                },
            )
            target = FinancePaymentMode.objects.create(**payload)
            self._touch_audit_fields(target)
            self._mark_copied('financePaymentModes')

    def _copy_finance_configuration(self):
        queryset = FinanceConfiguration.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).order_by('id')
        queryset = self._filter_queryset('copy_finance_configuration', queryset)
        for row in queryset:
            existing = FinanceConfiguration.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                isDeleted=False,
            ).order_by('id').first()
            if existing:
                self._mark_skipped('financeConfiguration')
                continue
            payload = _clone_payload(
                row,
                exclude={'id', 'sessionID', 'schoolID', 'defaultCashAccountID', 'defaultBankAccountID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={
                    'sessionID': self.target_session,
                    'schoolID_id': self.school_id,
                    'defaultCashAccountID_id': self.finance_account_map.get(row.defaultCashAccountID_id),
                    'defaultBankAccountID_id': self.finance_account_map.get(row.defaultBankAccountID_id),
                    'lastReceiptSequence': 0,
                    'lastVoucherSequence': 0,
                    'lastRefundSequence': 0,
                    'lastTransactionSequence': 0,
                    'lastPayrollSequence': 0,
                },
            )
            target = FinanceConfiguration.objects.create(**payload)
            self._touch_audit_fields(target)
            self._mark_copied('financeConfiguration')

    def _copy_fee_heads(self):
        queryset = FeeHead.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).order_by('displayOrder', 'name', 'id')
        queryset = self._filter_queryset('copy_fee_heads', queryset)
        for row in queryset:
            income_account_id = self.finance_account_map.get(row.incomeAccountID_id)
            receivable_account_id = self.finance_account_map.get(row.receivableAccountID_id)
            late_fee_account_id = self.finance_account_map.get(row.lateFeeAccountID_id)
            if not income_account_id or not receivable_account_id:
                self.summary['warnings'].append('Some fee heads were skipped because required finance accounts were unavailable in the target session.')
                self._mark_skipped('feeHeads')
                continue
            existing = FeeHead.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                isDeleted=False,
            ).filter(_text_match_q('code', row.code)).order_by('id').first()
            if existing:
                self._mark_skipped('feeHeads')
                continue
            payload = _clone_payload(
                row,
                exclude={'id', 'sessionID', 'schoolID', 'incomeAccountID', 'receivableAccountID', 'lateFeeAccountID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={
                    'sessionID': self.target_session,
                    'schoolID_id': self.school_id,
                    'incomeAccountID_id': income_account_id,
                    'receivableAccountID_id': receivable_account_id,
                    'lateFeeAccountID_id': late_fee_account_id,
                },
            )
            target = FeeHead.objects.create(**payload)
            self._touch_audit_fields(target)
            self._mark_copied('feeHeads')

    def _copy_expense_categories(self):
        queryset = ExpenseCategory.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).order_by('name', 'id')
        queryset = self._filter_queryset('copy_expense_categories', queryset)
        for row in queryset:
            expense_account_id = self.finance_account_map.get(row.expenseAccountID_id)
            payable_account_id = self.finance_account_map.get(row.payableAccountID_id)
            if not expense_account_id:
                self.summary['warnings'].append('Some expense categories were skipped because their expense account was unavailable in the target session.')
                self._mark_skipped('expenseCategories')
                continue
            existing = ExpenseCategory.objects.filter(
                schoolID_id=self.school_id,
                isDeleted=False,
            ).filter(_text_match_q('code', row.code)).order_by('id').first()
            if existing:
                self._mark_skipped('expenseCategories')
                continue
            payload = _clone_payload(
                row,
                exclude={'id', 'sessionID', 'schoolID', 'expenseAccountID', 'payableAccountID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={
                    'sessionID': self.target_session,
                    'schoolID_id': self.school_id,
                    'expenseAccountID_id': expense_account_id,
                    'payableAccountID_id': payable_account_id,
                },
            )
            target = ExpenseCategory.objects.create(**payload)
            self._touch_audit_fields(target)
            self._mark_copied('expenseCategories')

    def _copy_finance_approval_rules(self):
        queryset = FinanceApprovalRule.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).order_by('documentType', 'priority', 'id')
        queryset = self._filter_queryset('copy_finance_approval_rules', queryset)
        for row in queryset:
            existing = FinanceApprovalRule.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                documentType=row.documentType,
                priority=row.priority,
                isDeleted=False,
            ).filter(_text_match_q('ruleName', row.ruleName)).order_by('id').first()
            if existing:
                self._mark_skipped('financeApprovalRules')
                continue
            payload = _clone_payload(
                row,
                exclude={'id', 'sessionID', 'schoolID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={'sessionID': self.target_session, 'schoolID_id': self.school_id},
            )
            target = FinanceApprovalRule.objects.create(**payload)
            self._touch_audit_fields(target)
            self._mark_copied('financeApprovalRules')

    def _copy_transport_setup(self):
        self._copy_transport_routes()
        self._copy_transport_stops()
        self._copy_transport_drivers()
        self._copy_transport_vehicles()
        self._copy_transport_fee_mappings()

    def _copy_transport_routes(self):
        queryset = TransportRoute.objects.filter(schoolID_id=self.school_id, sessionID_id=self.source_session.id, isDeleted=False).order_by('routeCode', 'id')
        queryset = self._filter_queryset('copy_transport_routes', queryset)
        for row in queryset:
            existing = TransportRoute.objects.filter(schoolID_id=self.school_id, sessionID_id=self.target_session.id, isDeleted=False).filter(_text_match_q('routeCode', row.routeCode)).order_by('id').first()
            if existing:
                self.transport_route_map[row.id] = existing.id
                self._mark_skipped('transportRoutes')
                continue
            payload = _clone_payload(row, exclude={'id', 'sessionID', 'schoolID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'}, overrides={'sessionID': self.target_session, 'schoolID_id': self.school_id})
            target = TransportRoute.objects.create(**payload)
            self._touch_audit_fields(target)
            self.transport_route_map[row.id] = target.id
            self._mark_copied('transportRoutes')

    def _copy_transport_stops(self):
        queryset = TransportStop.objects.filter(schoolID_id=self.school_id, sessionID_id=self.source_session.id, isDeleted=False).order_by('routeID_id', 'displayOrder', 'id')
        queryset = self._filter_queryset('copy_transport_stops', queryset)
        for row in queryset:
            route_id = self.transport_route_map.get(row.routeID_id)
            if not route_id:
                self._mark_skipped('transportStops')
                continue
            existing = TransportStop.objects.filter(routeID_id=route_id, isDeleted=False).filter(_text_match_q('stopName', row.stopName)).order_by('id').first()
            if existing:
                self.transport_stop_map[row.id] = existing.id
                self._mark_skipped('transportStops')
                continue
            payload = _clone_payload(row, exclude={'id', 'sessionID', 'schoolID', 'routeID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'}, overrides={'sessionID': self.target_session, 'schoolID_id': self.school_id, 'routeID_id': route_id})
            target = TransportStop.objects.create(**payload)
            self._touch_audit_fields(target)
            self.transport_stop_map[row.id] = target.id
            self._mark_copied('transportStops')

    def _copy_transport_drivers(self):
        queryset = TransportDriver.objects.filter(schoolID_id=self.school_id, sessionID_id=self.source_session.id, isDeleted=False).order_by('name', 'id')
        queryset = self._filter_queryset('copy_transport_drivers', queryset)
        for row in queryset:
            existing = TransportDriver.objects.filter(schoolID_id=self.school_id, sessionID_id=self.target_session.id, isDeleted=False).filter(_text_match_q('name', row.name)).filter(_text_match_q('phoneNumber', row.phoneNumber)).order_by('id').first()
            if existing:
                self.transport_driver_map[row.id] = existing.id
                self._mark_skipped('transportDrivers')
                continue
            payload = _clone_payload(row, exclude={'id', 'sessionID', 'schoolID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'}, overrides={'sessionID': self.target_session, 'schoolID_id': self.school_id})
            target = TransportDriver.objects.create(**payload)
            self._touch_audit_fields(target)
            self.transport_driver_map[row.id] = target.id
            self._mark_copied('transportDrivers')

    def _copy_transport_vehicles(self):
        queryset = TransportVehicle.objects.filter(schoolID_id=self.school_id, sessionID_id=self.source_session.id, isDeleted=False).order_by('vehicleNumber', 'id')
        queryset = self._filter_queryset('copy_transport_vehicles', queryset)
        for row in queryset:
            existing = TransportVehicle.objects.filter(schoolID_id=self.school_id, sessionID_id=self.target_session.id, isDeleted=False).filter(_text_match_q('vehicleNumber', row.vehicleNumber)).order_by('id').first()
            if existing:
                self.transport_vehicle_map[row.id] = existing.id
                self._mark_skipped('transportVehicles')
                continue
            payload = _clone_payload(
                row,
                exclude={'id', 'sessionID', 'schoolID', 'driverID', 'routeID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={
                    'sessionID': self.target_session,
                    'schoolID_id': self.school_id,
                    'driverID_id': self.transport_driver_map.get(row.driverID_id),
                    'routeID_id': self.transport_route_map.get(row.routeID_id),
                },
            )
            target = TransportVehicle.objects.create(**payload)
            self._touch_audit_fields(target)
            self.transport_vehicle_map[row.id] = target.id
            self._mark_copied('transportVehicles')

    def _copy_transport_fee_mappings(self):
        queryset = TransportFeeMapping.objects.filter(schoolID_id=self.school_id, sessionID_id=self.source_session.id, isDeleted=False).order_by('routeID_id', 'stopID_id', 'id')
        queryset = self._filter_queryset('copy_transport_fee_mappings', queryset)
        for row in queryset:
            route_id = self.transport_route_map.get(row.routeID_id)
            stop_id = self.transport_stop_map.get(row.stopID_id)
            if not route_id or (row.stopID_id and not stop_id):
                self._mark_skipped('transportFeeMappings')
                continue
            existing = TransportFeeMapping.objects.filter(schoolID_id=self.school_id, sessionID_id=self.target_session.id, routeID_id=route_id, stopID_id=stop_id, assigneeType=row.assigneeType, feeMode=row.feeMode, isDeleted=False).order_by('id').first()
            if existing:
                self._mark_skipped('transportFeeMappings')
                continue
            payload = _clone_payload(row, exclude={'id', 'sessionID', 'schoolID', 'routeID', 'stopID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'}, overrides={'sessionID': self.target_session, 'schoolID_id': self.school_id, 'routeID_id': route_id, 'stopID_id': stop_id})
            target = TransportFeeMapping.objects.create(**payload)
            self._touch_audit_fields(target)
            self._mark_copied('transportFeeMappings')

    def _copy_hostel_setup(self):
        self._copy_hostel_buildings()
        self._copy_hostel_floors()
        self._copy_hostel_room_types()
        self._copy_hostel_rooms()
        self._copy_hostel_beds()
        self._copy_hostel_fee_mappings()

    def _copy_hostel_buildings(self):
        queryset = HostelBuilding.objects.filter(schoolID_id=self.school_id, sessionID_id=self.source_session.id, isDeleted=False).order_by('buildingCode', 'id')
        queryset = self._filter_queryset('copy_hostel_buildings', queryset)
        for row in queryset:
            existing = HostelBuilding.objects.filter(schoolID_id=self.school_id, sessionID_id=self.target_session.id, isDeleted=False).filter(_text_match_q('buildingCode', row.buildingCode)).order_by('id').first()
            if existing:
                self.hostel_building_map[row.id] = existing.id
                self._mark_skipped('hostelBuildings')
                continue
            payload = _clone_payload(row, exclude={'id', 'sessionID', 'schoolID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'}, overrides={'sessionID': self.target_session, 'schoolID_id': self.school_id})
            target = HostelBuilding.objects.create(**payload)
            self._touch_audit_fields(target)
            self.hostel_building_map[row.id] = target.id
            self._mark_copied('hostelBuildings')

    def _copy_hostel_floors(self):
        queryset = HostelFloor.objects.filter(schoolID_id=self.school_id, sessionID_id=self.source_session.id, isDeleted=False).order_by('buildingID_id', 'displayOrder', 'id')
        queryset = self._filter_queryset('copy_hostel_floors', queryset)
        for row in queryset:
            building_id = self.hostel_building_map.get(row.buildingID_id)
            if not building_id:
                self._mark_skipped('hostelFloors')
                continue
            existing = HostelFloor.objects.filter(buildingID_id=building_id, isDeleted=False).filter(_text_match_q('floorName', row.floorName)).order_by('id').first()
            if existing:
                self.hostel_floor_map[row.id] = existing.id
                self._mark_skipped('hostelFloors')
                continue
            payload = _clone_payload(row, exclude={'id', 'sessionID', 'schoolID', 'buildingID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'}, overrides={'sessionID': self.target_session, 'schoolID_id': self.school_id, 'buildingID_id': building_id})
            target = HostelFloor.objects.create(**payload)
            self._touch_audit_fields(target)
            self.hostel_floor_map[row.id] = target.id
            self._mark_copied('hostelFloors')

    def _copy_hostel_room_types(self):
        queryset = HostelRoomType.objects.filter(schoolID_id=self.school_id, sessionID_id=self.source_session.id, isDeleted=False).order_by('name', 'id')
        queryset = self._filter_queryset('copy_hostel_room_types', queryset)
        for row in queryset:
            existing = HostelRoomType.objects.filter(schoolID_id=self.school_id, sessionID_id=self.target_session.id, isDeleted=False).filter(_text_match_q('name', row.name)).order_by('id').first()
            if existing:
                self.hostel_room_type_map[row.id] = existing.id
                self._mark_skipped('hostelRoomTypes')
                continue
            payload = _clone_payload(row, exclude={'id', 'sessionID', 'schoolID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'}, overrides={'sessionID': self.target_session, 'schoolID_id': self.school_id})
            target = HostelRoomType.objects.create(**payload)
            self._touch_audit_fields(target)
            self.hostel_room_type_map[row.id] = target.id
            self._mark_copied('hostelRoomTypes')

    def _copy_hostel_rooms(self):
        queryset = HostelRoom.objects.filter(schoolID_id=self.school_id, sessionID_id=self.source_session.id, isDeleted=False).order_by('buildingID_id', 'roomNumber', 'id')
        queryset = self._filter_queryset('copy_hostel_rooms', queryset)
        for row in queryset:
            building_id = self.hostel_building_map.get(row.buildingID_id)
            floor_id = self.hostel_floor_map.get(row.floorID_id)
            room_type_id = self.hostel_room_type_map.get(row.roomTypeID_id)
            if not building_id or (row.floorID_id and not floor_id) or (row.roomTypeID_id and not room_type_id):
                self._mark_skipped('hostelRooms')
                continue
            existing = HostelRoom.objects.filter(buildingID_id=building_id, isDeleted=False).filter(_text_match_q('roomNumber', row.roomNumber)).order_by('id').first()
            if existing:
                self.hostel_room_map[row.id] = existing.id
                self._mark_skipped('hostelRooms')
                continue
            payload = _clone_payload(row, exclude={'id', 'sessionID', 'schoolID', 'buildingID', 'floorID', 'roomTypeID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'}, overrides={'sessionID': self.target_session, 'schoolID_id': self.school_id, 'buildingID_id': building_id, 'floorID_id': floor_id, 'roomTypeID_id': room_type_id})
            target = HostelRoom.objects.create(**payload)
            self._touch_audit_fields(target)
            self.hostel_room_map[row.id] = target.id
            self._mark_copied('hostelRooms')

    def _copy_hostel_beds(self):
        queryset = HostelBed.objects.filter(schoolID_id=self.school_id, sessionID_id=self.source_session.id, isDeleted=False).order_by('roomID_id', 'bedNumber', 'id')
        queryset = self._filter_queryset('copy_hostel_beds', queryset)
        for row in queryset:
            room_id = self.hostel_room_map.get(row.roomID_id)
            if not room_id:
                self._mark_skipped('hostelBeds')
                continue
            existing = HostelBed.objects.filter(roomID_id=room_id, isDeleted=False).filter(_text_match_q('bedNumber', row.bedNumber)).order_by('id').first()
            if existing:
                self.hostel_bed_map[row.id] = existing.id
                self._mark_skipped('hostelBeds')
                continue
            payload = _clone_payload(row, exclude={'id', 'sessionID', 'schoolID', 'roomID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'}, overrides={'sessionID': self.target_session, 'schoolID_id': self.school_id, 'roomID_id': room_id, 'status': 'available'})
            target = HostelBed.objects.create(**payload)
            self._touch_audit_fields(target)
            self.hostel_bed_map[row.id] = target.id
            self._mark_copied('hostelBeds')

    def _copy_hostel_fee_mappings(self):
        queryset = HostelFeeMapping.objects.filter(schoolID_id=self.school_id, sessionID_id=self.source_session.id, isDeleted=False).order_by('buildingID_id', 'roomTypeID_id', 'roomID_id', 'id')
        queryset = self._filter_queryset('copy_hostel_fee_mappings', queryset)
        for row in queryset:
            building_id = self.hostel_building_map.get(row.buildingID_id)
            room_type_id = self.hostel_room_type_map.get(row.roomTypeID_id)
            room_id = self.hostel_room_map.get(row.roomID_id)
            if (row.buildingID_id and not building_id) or (row.roomTypeID_id and not room_type_id) or (row.roomID_id and not room_id):
                self._mark_skipped('hostelFeeMappings')
                continue
            existing = HostelFeeMapping.objects.filter(schoolID_id=self.school_id, sessionID_id=self.target_session.id, buildingID_id=building_id, roomTypeID_id=room_type_id, roomID_id=room_id, isDeleted=False).order_by('id').first()
            if existing:
                self._mark_skipped('hostelFeeMappings')
                continue
            payload = _clone_payload(row, exclude={'id', 'sessionID', 'schoolID', 'buildingID', 'roomTypeID', 'roomID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'}, overrides={'sessionID': self.target_session, 'schoolID_id': self.school_id, 'buildingID_id': building_id, 'roomTypeID_id': room_type_id, 'roomID_id': room_id})
            target = HostelFeeMapping.objects.create(**payload)
            self._touch_audit_fields(target)
            self._mark_copied('hostelFeeMappings')

    def _copy_library_setup(self):
        self._copy_library_categories()
        self._copy_library_authors()
        self._copy_library_publishers()
        self._copy_library_books()
        self._copy_library_card_designs()
        self._copy_library_settings()

    def _copy_library_categories(self):
        queryset = LibraryCategory.objects.filter(schoolID_id=self.school_id, sessionID_id=self.source_session.id, isDeleted=False).order_by('parent_id', 'name', 'id')
        queryset = self._filter_queryset('copy_library_categories', queryset)
        deferred_parents = []
        for row in queryset:
            existing = LibraryCategory.objects.filter(schoolID_id=self.school_id, sessionID_id=self.target_session.id, isDeleted=False).filter(_text_match_q('name', row.name)).order_by('id').first()
            if existing:
                self.library_category_map[row.id] = existing.id
                self._mark_skipped('libraryCategories')
                continue
            payload = _clone_payload(row, exclude={'id', 'sessionID', 'schoolID', 'parent', 'updatedByUserID', 'lastUpdatedOn', 'datetime'}, overrides={'sessionID': self.target_session, 'schoolID_id': self.school_id, 'parent_id': None})
            target = LibraryCategory.objects.create(**payload)
            self._touch_audit_fields(target)
            self.library_category_map[row.id] = target.id
            deferred_parents.append((target, row.parent_id))
            self._mark_copied('libraryCategories')
        for target, source_parent_id in deferred_parents:
            parent_id = self.library_category_map.get(source_parent_id)
            if parent_id:
                target.parent_id = parent_id
                target.save(update_fields=['parent', 'lastUpdatedOn'])

    def _copy_library_authors(self):
        queryset = LibraryAuthor.objects.filter(schoolID_id=self.school_id, sessionID_id=self.source_session.id, isDeleted=False).order_by('name', 'id')
        queryset = self._filter_queryset('copy_library_authors', queryset)
        for row in queryset:
            existing = LibraryAuthor.objects.filter(schoolID_id=self.school_id, sessionID_id=self.target_session.id, isDeleted=False).filter(_text_match_q('name', row.name)).order_by('id').first()
            if existing:
                self.library_author_map[row.id] = existing.id
                self._mark_skipped('libraryAuthors')
                continue
            payload = _clone_payload(row, exclude={'id', 'sessionID', 'schoolID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'}, overrides={'sessionID': self.target_session, 'schoolID_id': self.school_id})
            target = LibraryAuthor.objects.create(**payload)
            self._touch_audit_fields(target)
            self.library_author_map[row.id] = target.id
            self._mark_copied('libraryAuthors')

    def _copy_library_publishers(self):
        queryset = LibraryPublisher.objects.filter(schoolID_id=self.school_id, sessionID_id=self.source_session.id, isDeleted=False).order_by('name', 'id')
        queryset = self._filter_queryset('copy_library_publishers', queryset)
        for row in queryset:
            existing = LibraryPublisher.objects.filter(schoolID_id=self.school_id, sessionID_id=self.target_session.id, isDeleted=False).filter(_text_match_q('name', row.name)).order_by('id').first()
            if existing:
                self.library_publisher_map[row.id] = existing.id
                self._mark_skipped('libraryPublishers')
                continue
            payload = _clone_payload(row, exclude={'id', 'sessionID', 'schoolID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'}, overrides={'sessionID': self.target_session, 'schoolID_id': self.school_id})
            target = LibraryPublisher.objects.create(**payload)
            self._touch_audit_fields(target)
            self.library_publisher_map[row.id] = target.id
            self._mark_copied('libraryPublishers')

    def _copy_library_books(self):
        queryset = LibraryBook.objects.filter(schoolID_id=self.school_id, sessionID_id=self.source_session.id, isDeleted=False).order_by('title', 'id')
        queryset = self._filter_queryset('copy_library_books', queryset)
        for row in queryset:
            category_id = self.library_category_map.get(row.category_id)
            publisher_id = self.library_publisher_map.get(row.publisher_id)
            existing_qs = LibraryBook.objects.filter(schoolID_id=self.school_id, sessionID_id=self.target_session.id, isDeleted=False)
            existing = None
            if row.isbn:
                existing = existing_qs.filter(_text_match_q('isbn', row.isbn)).order_by('id').first()
            if not existing:
                existing = existing_qs.filter(_text_match_q('title', row.title)).order_by('id').first()
            if existing:
                self.library_book_map[row.id] = existing.id
                self._mark_skipped('libraryBooks')
                continue
            payload = _clone_payload(row, exclude={'id', 'sessionID', 'schoolID', 'category', 'publisher', 'updatedByUserID', 'lastUpdatedOn', 'datetime'}, overrides={'sessionID': self.target_session, 'schoolID_id': self.school_id, 'category_id': category_id, 'publisher_id': publisher_id})
            target = LibraryBook.objects.create(**payload)
            self._touch_audit_fields(target)
            self.library_book_map[row.id] = target.id
            for source_author_id in LibraryBookAuthor.objects.filter(book_id=row.id).values_list('author_id', flat=True):
                author_id = self.library_author_map.get(source_author_id)
                if author_id:
                    LibraryBookAuthor.objects.get_or_create(book=target, author_id=author_id)
            self._mark_copied('libraryBooks')

    def _copy_library_card_designs(self):
        queryset = LibraryMemberCardDesign.objects.filter(schoolID_id=self.school_id, sessionID_id=self.source_session.id, isDeleted=False).order_by('name', 'id')
        queryset = self._filter_queryset('copy_library_card_designs', queryset)
        for row in queryset:
            existing = LibraryMemberCardDesign.objects.filter(schoolID_id=self.school_id, sessionID_id=self.target_session.id, isDeleted=False).filter(_text_match_q('name', row.name)).order_by('id').first()
            if existing:
                self._mark_skipped('libraryCardDesigns')
                continue
            payload = _clone_payload(row, exclude={'id', 'sessionID', 'schoolID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'}, overrides={'sessionID': self.target_session, 'schoolID_id': self.school_id})
            target = LibraryMemberCardDesign.objects.create(**payload)
            self._touch_audit_fields(target)
            self._mark_copied('libraryCardDesigns')

    def _copy_library_settings(self):
        queryset = LibrarySetting.objects.filter(schoolID_id=self.school_id, sessionID_id=self.source_session.id, isDeleted=False).order_by('id')
        queryset = self._filter_queryset('copy_library_settings', queryset)
        for row in queryset:
            existing = LibrarySetting.objects.filter(schoolID_id=self.school_id, sessionID_id=self.target_session.id, isDeleted=False).order_by('id').first()
            if existing:
                self._mark_skipped('librarySettings')
                continue
            payload = _clone_payload(row, exclude={'id', 'sessionID', 'schoolID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'}, overrides={'sessionID': self.target_session, 'schoolID_id': self.school_id})
            target = LibrarySetting.objects.create(**payload)
            self._touch_audit_fields(target)
            self._mark_copied('librarySettings')

    def _copy_inventory_setup(self):
        self._copy_inventory_categories()
        self._copy_inventory_suppliers()
        self._copy_inventory_items()

    def _copy_inventory_categories(self):
        queryset = InventoryCategory.objects.filter(schoolID_id=self.school_id, sessionID_id=self.source_session.id, isDeleted=False).order_by('name', 'id')
        queryset = self._filter_queryset('copy_inventory_categories', queryset)
        for row in queryset:
            existing = InventoryCategory.objects.filter(schoolID_id=self.school_id, sessionID_id=self.target_session.id, isDeleted=False).filter(_text_match_q('name', row.name)).order_by('id').first()
            if existing:
                self.inventory_category_map[row.id] = existing.id
                self._mark_skipped('inventoryCategories')
                continue
            payload = _clone_payload(row, exclude={'id', 'sessionID', 'schoolID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'}, overrides={'sessionID': self.target_session, 'schoolID_id': self.school_id})
            target = InventoryCategory.objects.create(**payload)
            self._touch_audit_fields(target)
            self.inventory_category_map[row.id] = target.id
            self._mark_copied('inventoryCategories')

    def _copy_inventory_suppliers(self):
        queryset = InventorySupplier.objects.filter(schoolID_id=self.school_id, sessionID_id=self.source_session.id, isDeleted=False).order_by('companyName', 'id')
        queryset = self._filter_queryset('copy_inventory_suppliers', queryset)
        for row in queryset:
            existing = InventorySupplier.objects.filter(schoolID_id=self.school_id, sessionID_id=self.target_session.id, isDeleted=False).filter(_text_match_q('companyName', row.companyName)).order_by('id').first()
            if existing:
                self.inventory_supplier_map[row.id] = existing.id
                self._mark_skipped('inventorySuppliers')
                continue
            payload = _clone_payload(row, exclude={'id', 'sessionID', 'schoolID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'}, overrides={'sessionID': self.target_session, 'schoolID_id': self.school_id})
            target = InventorySupplier.objects.create(**payload)
            self._touch_audit_fields(target)
            self.inventory_supplier_map[row.id] = target.id
            self._mark_copied('inventorySuppliers')

    def _copy_inventory_items(self):
        queryset = InventoryItem.objects.filter(schoolID_id=self.school_id, sessionID_id=self.source_session.id, isDeleted=False).order_by('categoryID_id', 'name', 'id')
        queryset = self._filter_queryset('copy_inventory_items', queryset)
        for row in queryset:
            category_id = self.inventory_category_map.get(row.categoryID_id)
            if not category_id:
                self._mark_skipped('inventoryItems')
                continue
            existing = InventoryItem.objects.filter(schoolID_id=self.school_id, sessionID_id=self.target_session.id, categoryID_id=category_id, isDeleted=False).filter(_text_match_q('name', row.name)).order_by('id').first()
            if existing:
                self.inventory_item_map[row.id] = existing.id
                self._mark_skipped('inventoryItems')
                continue
            payload = _clone_payload(row, exclude={'id', 'sessionID', 'schoolID', 'categoryID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'}, overrides={'sessionID': self.target_session, 'schoolID_id': self.school_id, 'categoryID_id': category_id, 'currentStockQty': 0})
            target = InventoryItem.objects.create(**payload)
            self._touch_audit_fields(target)
            self.inventory_item_map[row.id] = target.id
            self._mark_copied('inventoryItems')

    def _ensure_profile_maps_for_cross_module_setup(self):
        if not self.teacher_map:
            for row in TeacherDetail.objects.filter(schoolID_id=self.school_id, sessionID_id=self.source_session.id, isDeleted=False):
                target_qs = TeacherDetail.objects.filter(schoolID_id=self.school_id, sessionID_id=self.target_session.id, isDeleted=False)
                if row.userID_id:
                    target_qs = target_qs.filter(userID_id=row.userID_id)
                elif row.employeeCode:
                    target_qs = target_qs.filter(_text_match_q('employeeCode', row.employeeCode))
                else:
                    target_qs = target_qs.filter(_text_match_q('name', row.name)).filter(_text_match_q('phoneNumber', row.phoneNumber))
                target = target_qs.order_by('id').first()
                if target:
                    self.teacher_map[row.id] = target.id
        if not self.parent_map:
            for row in Parent.objects.filter(schoolID_id=self.school_id, sessionID_id=self.source_session.id, isDeleted=False):
                target = Parent.objects.filter(
                    schoolID_id=self.school_id,
                    sessionID_id=self.target_session.id,
                    isDeleted=False,
                ).filter(_text_match_q('fatherName', row.fatherName)).filter(_text_match_q('motherName', row.motherName)).filter(_text_match_q('phoneNumber', row.phoneNumber)).order_by('id').first()
                if target:
                    self.parent_map[row.id] = target.id
        if not self.student_map:
            for row in Student.objects.filter(schoolID_id=self.school_id, sessionID_id=self.source_session.id, isDeleted=False):
                target_qs = Student.objects.filter(schoolID_id=self.school_id, sessionID_id=self.target_session.id, isDeleted=False)
                if row.userID_id:
                    target_qs = target_qs.filter(userID_id=row.userID_id)
                elif row.registrationCode:
                    target_qs = target_qs.filter(_text_match_q('registrationCode', row.registrationCode))
                else:
                    target_qs = target_qs.filter(_text_match_q('name', row.name)).filter(dob=row.dob)
                target = target_qs.order_by('id').first()
                if target:
                    self.student_map[row.id] = target.id

    def _copy_parents(self):
        queryset = Parent.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).order_by('id')
        queryset = self._filter_queryset('copy_parents', queryset)
        for row in queryset:
            existing = Parent.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                isDeleted=False,
            ).filter(_text_match_q('fatherName', row.fatherName)).filter(_text_match_q('motherName', row.motherName)).filter(_text_match_q('phoneNumber', row.phoneNumber)).order_by('id').first()
            if existing:
                self.parent_map[row.id] = existing.id
                self._mark_skipped('parents')
                continue

            payload = _clone_payload(
                row,
                exclude={'id', 'sessionID', 'schoolID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={'sessionID': self.target_session, 'schoolID_id': self.school_id},
            )
            target = Parent.objects.create(**payload)
            self._touch_audit_fields(target)
            self.parent_map[row.id] = target.id
            self._mark_copied('parents')

    def _copy_students(self):
        queryset = Student.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).select_related('standardID').order_by('id')
        queryset = self._filter_queryset('copy_students', queryset)
        promotion_map = _build_student_target_class_map(
            source_session_id=self.source_session.id,
            target_session_id=self.target_session.id,
            promote_students=self.options['promote_students'],
            promotion_overrides=self.options.get('promotion_overrides'),
        )
        for row in queryset:
            target_standard_id = promotion_map.get(row.standardID_id)
            if row.standardID_id and not target_standard_id:
                self.summary['warnings'].append(f'Some students were skipped because target class mapping was not found for source class "{row.standardID.name if row.standardID else "N/A"}".')
                self._mark_skipped('students')
                continue
            existing = Student.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                isDeleted=False,
            )
            if row.userID_id:
                existing = existing.filter(userID_id=row.userID_id)
            elif row.registrationCode:
                existing = existing.filter(_text_match_q('registrationCode', row.registrationCode))
            else:
                existing = existing.filter(_text_match_q('name', row.name)).filter(dob=row.dob)
            target = existing.order_by('id').first()
            if target:
                self.student_map[row.id] = target.id
                self._mark_skipped('students')
                continue

            payload = _clone_payload(
                row,
                exclude={'id', 'sessionID', 'schoolID', 'parentID', 'standardID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={
                    'sessionID': self.target_session,
                    'schoolID_id': self.school_id,
                    'parentID_id': self.parent_map.get(row.parentID_id),
                    'standardID_id': target_standard_id,
                },
            )
            target = Student.objects.create(**payload)
            self._touch_audit_fields(target)
            self.student_map[row.id] = target.id
            self._mark_copied('students')

    def _touch_audit_fields(self, instance):
        if not self.acting_user:
            return
        editor_name = self.acting_user.get_full_name().strip() or self.acting_user.username
        instance.updatedByUserID_id = self.acting_user.id
        instance.lastEditedBy = editor_name
        instance.save(update_fields=['updatedByUserID', 'lastEditedBy', 'lastUpdatedOn'])

    def _mark_copied(self, key):
        self.summary['copied'][key] = self.summary['copied'].get(key, 0) + 1

    def _mark_skipped(self, key):
        self.summary['skipped'][key] = self.summary['skipped'].get(key, 0) + 1


def _validate_sessions(*, school_id, source_session_id, target_session_id):
    source_session = SchoolSession.objects.filter(
        pk=source_session_id,
        schoolID_id=school_id,
        isDeleted=False,
    ).first()
    target_session = SchoolSession.objects.filter(
        pk=target_session_id,
        schoolID_id=school_id,
        isDeleted=False,
    ).first()

    if not source_session:
        raise ValueError('Source session was not found for the current school.')
    if not target_session:
        raise ValueError('Target session was not found for the current school.')
    if source_session.id == target_session.id:
        raise ValueError('Source session and target session must be different.')
    if not _is_target_session_newer(source_session, target_session):
        raise ValueError('Import is allowed only from an older session into a newer session.')
    if not target_session.isCurrent:
        raise ValueError('Target session must be the current session.')
    previous_session = _previous_session_for_school(school_id, target_session.id)
    if not previous_session:
        raise ValueError('No previous session is available before the current session.')
    if source_session.id != previous_session.id:
        raise ValueError('Source session must be the immediate previous session.')
    return source_session, target_session


def _validate_target_session_state(target_session, options):
    warnings = []
    checks = [
        ('copy_teachers', TeacherDetail, 'teachers'),
        ('copy_classes', Standard, 'classes'),
        ('copy_subjects', Subjects, 'subjects'),
        ('copy_students', Student, 'students'),
        ('copy_exam_setup', Exam, 'exams'),
        ('copy_holidays', SchoolHoliday, 'holidays'),
        ('copy_id_card_designs', StudentIdCardDesign, 'ID card designs'),
        ('copy_timetable_setup', SchoolTimetable, 'timetables'),
        ('copy_finance_setup', FinanceAccount, 'finance setup rows'),
        ('copy_transport_setup', TransportRoute, 'transport setup rows'),
        ('copy_hostel_setup', HostelBuilding, 'hostel setup rows'),
        ('copy_library_setup', LibraryCategory, 'library setup rows'),
        ('copy_inventory_setup', InventoryCategory, 'inventory setup rows'),
    ]
    for option_key, model, label in checks:
        if not options.get(option_key):
            continue
        if model.objects.filter(sessionID_id=target_session.id, isDeleted=False).exists():
            warnings.append(f'Target session already has active {label}. Existing rows will be skipped when duplicates are detected.')
    return warnings


def _serialize_session(session_obj):
    return {
        'id': session_obj.id,
        'sessionYear': session_obj.sessionYear,
        'startDate': session_obj.startDate.isoformat() if session_obj.startDate else None,
        'endDate': session_obj.endDate.isoformat() if session_obj.endDate else None,
        'isCurrent': bool(session_obj.isCurrent),
    }


def _clone_payload(instance, *, exclude=None, overrides=None):
    exclude = set(exclude or set())
    overrides = overrides or {}
    payload = {}
    for field in instance._meta.concrete_fields:
        if field.name in exclude:
            continue
        if field.primary_key:
            continue
        if field.is_relation:
            payload[field.attname] = getattr(instance, field.attname)
        else:
            payload[field.name] = getattr(instance, field.name)
    payload.update(overrides)
    return payload


def _normalize_text(value):
    return re.sub(r'[^a-z0-9]+', ' ', (value or '').strip().lower()).strip()


def _text_match_q(field_name, value):
    normalized = (value or '').strip()
    if not normalized:
        return Q(**{f'{field_name}__isnull': True}) | Q(**{field_name: ''})
    return Q(**{f'{field_name}__iexact': normalized})


def _class_rank(standard):
    normalized = _normalize_text(getattr(standard, 'name', ''))
    if not normalized:
        return None

    direct_patterns = [
        (r'\bpre[\s-]*nursery\b', -4),
        (r'\bnursery\b', -3),
        (r'\blkg\b|\blower\s*kg\b', -2),
        (r'\bukg\b|\bupper\s*kg\b|\bprep\b', -1),
        (r'\bplay\b|\bplaygroup\b', -5),
    ]
    for pattern, rank in direct_patterns:
        if re.search(pattern, normalized):
            return rank

    digit_match = re.search(r'\b(?:class|std|standard|grade)?\s*(\d{1,2})\b', normalized)
    if digit_match:
        return int(digit_match.group(1))

    roman_match = re.search(r'\b(?:class|std|standard|grade)?\s*(xii|xi|ix|iv|v?i{0,3}|x)\b', normalized)
    if roman_match:
        roman_value = _roman_to_int(roman_match.group(1).upper())
        if roman_value is not None:
            return roman_value

    word_map = {
        'one': 1,
        'two': 2,
        'three': 3,
        'four': 4,
        'five': 5,
        'six': 6,
        'seven': 7,
        'eight': 8,
        'nine': 9,
        'ten': 10,
        'eleven': 11,
        'twelve': 12,
    }
    for token in normalized.split():
        if token in word_map:
            return word_map[token]
    return None


def _is_target_session_newer(source_session, target_session):
    source_key = _session_order_key(source_session)
    target_key = _session_order_key(target_session)
    return source_key < target_key


def _session_order_key(session_obj):
    start_date = getattr(session_obj, 'startDate', None)
    end_date = getattr(session_obj, 'endDate', None)
    inferred_year = _infer_session_year(getattr(session_obj, 'sessionYear', None))
    anchor_date = start_date or end_date or date(inferred_year or 1900, 1, 1)
    return (anchor_date, inferred_year or anchor_date.year, session_obj.id)


def _infer_session_year(session_year_value):
    if not session_year_value:
        return None
    match = re.search(r'(19|20)\d{2}', str(session_year_value))
    if not match:
        return None
    return int(match.group(0))


def _previous_session_for_school(school_id, current_session_id):
    sessions = list(
        SchoolSession.objects.filter(
            schoolID_id=school_id,
            isDeleted=False,
        ).order_by('startDate', 'datetime', 'id')
    )
    ordered_ids = [item.id for item in sessions]
    if current_session_id not in ordered_ids:
        return None
    current_index = ordered_ids.index(current_session_id)
    if current_index <= 0:
        return None
    return sessions[current_index - 1]


def _roman_to_int(value):
    roman_map = {'I': 1, 'V': 5, 'X': 10}
    total = 0
    prev = 0
    for char in reversed(value):
        current = roman_map.get(char)
        if current is None:
            return None
        if current < prev:
            total -= current
        else:
            total += current
            prev = current
    return total


def _build_promotion_preview(source_session_id, target_session_id, promotion_overrides=None):
    warnings = []
    rows = []
    mapping = _build_student_target_class_map(
        source_session_id=source_session_id,
        target_session_id=target_session_id,
        promote_students=True,
        promotion_overrides=promotion_overrides,
        warnings=warnings,
    )
    source_classes = {
        row.id: row
        for row in Standard.objects.filter(sessionID_id=source_session_id, isDeleted=False).order_by('id')
    }
    target_classes = {
        row.id: row
        for row in Standard.objects.filter(sessionID_id=target_session_id, isDeleted=False).order_by('id')
    }
    for source_id, target_id in mapping.items():
        source_row = source_classes.get(source_id)
        target_row = target_classes.get(target_id)
        rows.append({
            'sourceClass': _class_label(source_row),
            'targetClass': _class_label(target_row),
        })
    return {'warnings': warnings, 'rows': rows}


def _build_student_target_class_map(*, source_session_id, target_session_id, promote_students, promotion_overrides=None, warnings=None):
    warnings = warnings if warnings is not None else []
    promotion_overrides = _normalize_promotion_overrides(promotion_overrides)
    source_classes = list(Standard.objects.filter(sessionID_id=source_session_id, isDeleted=False).order_by('id'))
    target_classes = list(Standard.objects.filter(sessionID_id=target_session_id, isDeleted=False).order_by('id'))
    target_by_exact = {
        (_normalize_text(row.name), _normalize_text(row.section)): row
        for row in target_classes
    }
    target_by_label = {
        _normalize_text(_class_label(row)): row
        for row in target_classes
    }
    if not promote_students:
        return {
            row.id: (target_by_exact.get((_normalize_text(row.name), _normalize_text(row.section))).id if target_by_exact.get((_normalize_text(row.name), _normalize_text(row.section))) else None)
            for row in source_classes
        }
    target_by_rank_section = {
        (_class_rank(row), _normalize_text(row.section)): row
        for row in target_classes
        if _class_rank(row) is not None
    }
    result = {}
    for row in source_classes:
        exact_key = (_normalize_text(row.name), _normalize_text(row.section))
        override_key = _normalize_text(_class_label(row))
        override_target_label = promotion_overrides.get(override_key)
        if override_target_label:
            override_target = target_by_label.get(_normalize_text(override_target_label))
            if override_target:
                result[row.id] = override_target.id
                continue
            result[row.id] = None
            warnings.append(f'Custom promotion target "{override_target_label}" was not found in the target session for "{_class_label(row)}".')
            continue
        current_target = target_by_exact.get(exact_key)
        current_rank = _class_rank(row)
        if not current_target:
            result[row.id] = None
            warnings.append(f'No matching class was found in the target session for "{_class_label(row)}".')
            continue
        if current_rank is None:
            result[row.id] = None
            warnings.append(f'Promotion needs a recognizable class name for "{_class_label(row)}". Use names like Nursery, LKG, UKG, or Class 1-12.')
            continue

        promoted_row = target_by_rank_section.get((current_rank + 1, _normalize_text(row.section)))
        if not promoted_row and _normalize_text(row.section):
            promoted_row = target_by_rank_section.get((current_rank + 1, ''))
        if not promoted_row:
            result[row.id] = None
            warnings.append(f'No next class was found in the target session for "{_class_label(row)}".')
            continue
        result[row.id] = promoted_row.id
    return result


def _normalize_promotion_overrides(overrides):
    normalized = {}
    if not overrides:
        return normalized
    for source_label, target_label in overrides.items():
        source_key = _normalize_text(source_label)
        target_value = (target_label or '').strip()
        if source_key and target_value:
            normalized[source_key] = target_value
    return normalized


def _class_label(standard):
    if not standard:
        return 'N/A'
    name = (standard.name or '').strip() or 'Unnamed Class'
    section = (standard.section or '').strip()
    return f'{name} - {section}' if section else name


def _dedupe_list(items):
    seen = set()
    ordered = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def _selected_ids_for_option(options, option_key):
    selection_overrides = (options or {}).get('selection_overrides') or {}
    values = selection_overrides.get(option_key) or []
    selected = []
    for value in values:
        try:
            selected.append(int(value))
        except (TypeError, ValueError):
            continue
    return selected


def _filter_preview_queryset(options, option_key, queryset):
    selected_ids = _selected_ids_for_option(options, option_key)
    if selected_ids:
        return queryset.filter(id__in=selected_ids)
    return queryset


def _preview_rows_for_option(option_key, queryset, selected_ids=None, limit=100):
    selected_ids = set(selected_ids or [])

    def with_selection(rows):
        for row in rows:
            row['selected'] = (row.get('id') in selected_ids) if selected_ids else True
        return rows

    handlers = {
        'copy_teachers': lambda qs: [
            {'id': row.id, 'label': row.name or 'N/A', 'meta': row.employeeCode or row.phoneNumber or ''}
            for row in qs.order_by('name', 'id')[:limit]
        ],
        'copy_classes': lambda qs: [
            {'id': row.id, 'label': _class_label(row), 'meta': row.classLocation or ''}
            for row in qs.order_by('name', 'section', 'id')[:limit]
        ],
        'copy_subjects': lambda qs: [
            {'id': row.id, 'label': row.name or 'N/A', 'meta': ''}
            for row in qs.order_by('name', 'id')[:limit]
        ],
        'copy_class_subjects': lambda qs: [
            {'id': row.id, 'label': f'{_class_label(row.standardID)} -> {row.subjectID.name if row.subjectID else "N/A"}', 'meta': ''}
            for row in qs.select_related('standardID', 'subjectID').order_by('standardID__name', 'subjectID__name', 'id')[:limit]
        ],
        'copy_teacher_subjects': lambda qs: [
            {'id': row.id, 'label': row.teacherID.name if row.teacherID else 'N/A', 'meta': _teacher_subject_meta(row)}
            for row in qs.select_related('teacherID', 'assignedSubjectID__standardID', 'assignedSubjectID__subjectID').order_by('teacherID__name', 'id')[:limit]
        ],
        'copy_exam_setup': lambda qs: [
            {'id': row.id, 'label': row.name or 'N/A', 'meta': ''}
            for row in qs.order_by('name', 'id')[:limit]
        ],
        'copy_exam_to_class': lambda qs: [
            {'id': row.id, 'label': f'{row.examID.name if row.examID else "N/A"} -> {_class_label(row.standardID)}', 'meta': ''}
            for row in qs.select_related('examID', 'standardID').order_by('examID__name', 'standardID__name', 'id')[:limit]
        ],
        'copy_exam_timetable': lambda qs: [
            {'id': row.id, 'label': f'{row.examID.name if row.examID else "N/A"} / {row.subjectID.name if row.subjectID else "N/A"}', 'meta': f'{_class_label(row.standardID)} | {row.examDate.isoformat() if row.examDate else "N/A"}'}
            for row in qs.select_related('examID', 'subjectID', 'standardID').order_by('examDate', 'id')[:limit]
        ],
        'copy_exam_component_types': lambda qs: [
            {'id': row.id, 'label': row.name or 'N/A', 'meta': row.code or ''}
            for row in qs.order_by('displayOrder', 'name', 'id')[:limit]
        ],
        'copy_component_rules': lambda qs: [
            {'id': row.id, 'label': _component_rule_label(row), 'meta': f'Max {row.maxMarks}, Pass {row.passMarks}'}
            for row in qs.select_related('examID__examID', 'subjectID__subjectID', 'componentTypeID').order_by('id')[:limit]
        ],
        'copy_co_scholastic_areas': lambda qs: [
            {'id': row.id, 'label': row.name or 'N/A', 'meta': row.code or ''}
            for row in qs.order_by('displayOrder', 'name', 'id')[:limit]
        ],
        'copy_grading_setup': lambda qs: [
            {'id': row.id, 'label': row.name or 'N/A', 'meta': 'Default' if row.isDefault else ''}
            for row in qs.order_by('name', 'id')[:limit]
        ],
        'copy_grading_bands': lambda qs: [
            {'id': row.id, 'label': row.gradeLabel or 'N/A', 'meta': f'{row.minPercentage}-{row.maxPercentage}%'}
            for row in qs.order_by('policyID__name', 'displayOrder', 'id')[:limit]
        ],
        'copy_pass_policies': lambda qs: [
            {'id': row.id, 'label': _pass_policy_label(row), 'meta': f'Overall pass: {row.overallPassMarks if row.overallPassMarks is not None else "Auto"}'}
            for row in qs.select_related('examID__examID').order_by('id')[:limit]
        ],
        'copy_leave_types': lambda qs: [
            {'id': row.id, 'label': row.name or 'N/A', 'meta': row.applicableFor or ''}
            for row in qs.order_by('name', 'id')[:limit]
        ],
        'copy_event_types': lambda qs: [
            {'id': row.id, 'label': row.name or 'N/A', 'meta': row.audience or ''}
            for row in qs.order_by('name', 'id')[:limit]
        ],
        'copy_holidays': lambda qs: [
            {'id': row.id, 'label': row.title or 'Holiday', 'meta': _date_range_label(row.startDate, row.endDate)}
            for row in qs.order_by('startDate', 'id')[:limit]
        ],
        'copy_id_card_designs': lambda qs: [
            {'id': row.id, 'label': row.name or 'Default ID Card Design', 'meta': 'Active' if row.isActive else 'Inactive'}
            for row in qs.order_by('name', 'id')[:limit]
        ],
        'copy_timetable_setup': lambda qs: [
            {'id': row.id, 'label': row.name or _class_label(row.standardID), 'meta': f'{_class_label(row.standardID)} | {row.status}'}
            for row in qs.select_related('standardID').order_by('standardID__name', 'name', 'id')[:limit]
        ],
        'copy_timetable_periods': lambda qs: [
            {'id': row.id, 'label': row.name or f'Period {row.displayOrder}', 'meta': _timetable_period_meta(row)}
            for row in qs.select_related('timetableID__standardID').order_by('timetableID_id', 'displayOrder', 'id')[:limit]
        ],
        'copy_timetable_entries': lambda qs: [
            {'id': row.id, 'label': _timetable_entry_label(row), 'meta': _timetable_entry_meta(row)}
            for row in qs.select_related('timetableID__standardID', 'periodID', 'assignedSubjectID__subjectID', 'teacherID').order_by('timetableID_id', 'dayOfWeek', 'periodID__displayOrder', 'id')[:limit]
        ],
        'copy_finance_accounts': lambda qs: [
            {'id': row.id, 'label': f'{row.accountCode} - {row.accountName}', 'meta': row.accountType}
            for row in qs.order_by('accountCode', 'id')[:limit]
        ],
        'copy_finance_parties': lambda qs: [
            {'id': row.id, 'label': row.displayName or 'N/A', 'meta': row.partyType}
            for row in qs.order_by('partyType', 'displayName', 'id')[:limit]
        ],
        'copy_finance_payment_modes': lambda qs: [
            {'id': row.id, 'label': row.name or 'N/A', 'meta': row.code or row.modeType}
            for row in qs.order_by('name', 'id')[:limit]
        ],
        'copy_finance_configuration': lambda qs: [
            {'id': row.id, 'label': row.receiptTitle or 'Finance Configuration', 'meta': f'Receipt prefix {row.receiptPrefix}'}
            for row in qs.order_by('id')[:limit]
        ],
        'copy_fee_heads': lambda qs: [
            {'id': row.id, 'label': row.name or 'N/A', 'meta': f'{row.code} | {row.category}'}
            for row in qs.order_by('displayOrder', 'name', 'id')[:limit]
        ],
        'copy_expense_categories': lambda qs: [
            {'id': row.id, 'label': row.name or 'N/A', 'meta': row.code or ''}
            for row in qs.order_by('name', 'id')[:limit]
        ],
        'copy_finance_approval_rules': lambda qs: [
            {'id': row.id, 'label': row.ruleName or 'Approval Rule', 'meta': f'{row.documentType} | Priority {row.priority}'}
            for row in qs.order_by('documentType', 'priority', 'id')[:limit]
        ],
        'copy_transport_routes': lambda qs: [
            {'id': row.id, 'label': row.routeName or 'N/A', 'meta': row.routeCode or ''}
            for row in qs.order_by('routeCode', 'id')[:limit]
        ],
        'copy_transport_stops': lambda qs: [
            {'id': row.id, 'label': row.stopName or 'N/A', 'meta': row.routeID.routeName if row.routeID else 'N/A'}
            for row in qs.select_related('routeID').order_by('routeID__routeCode', 'displayOrder', 'id')[:limit]
        ],
        'copy_transport_drivers': lambda qs: [
            {'id': row.id, 'label': row.name or 'N/A', 'meta': row.phoneNumber or row.licenseNumber or ''}
            for row in qs.order_by('name', 'id')[:limit]
        ],
        'copy_transport_vehicles': lambda qs: [
            {'id': row.id, 'label': row.vehicleNumber or 'N/A', 'meta': row.vehicleType}
            for row in qs.order_by('vehicleNumber', 'id')[:limit]
        ],
        'copy_transport_fee_mappings': lambda qs: [
            {'id': row.id, 'label': _transport_fee_mapping_label(row), 'meta': f'{row.feeMode} | {row.monthlyFee}'}
            for row in qs.select_related('routeID', 'stopID').order_by('routeID__routeCode', 'stopID__displayOrder', 'id')[:limit]
        ],
        'copy_hostel_buildings': lambda qs: [
            {'id': row.id, 'label': row.buildingName or 'N/A', 'meta': row.buildingCode or ''}
            for row in qs.order_by('buildingCode', 'id')[:limit]
        ],
        'copy_hostel_floors': lambda qs: [
            {'id': row.id, 'label': row.floorName or 'N/A', 'meta': row.buildingID.buildingName if row.buildingID else 'N/A'}
            for row in qs.select_related('buildingID').order_by('buildingID__buildingCode', 'displayOrder', 'id')[:limit]
        ],
        'copy_hostel_room_types': lambda qs: [
            {'id': row.id, 'label': row.name or 'N/A', 'meta': f'Capacity {row.capacity}'}
            for row in qs.order_by('name', 'id')[:limit]
        ],
        'copy_hostel_rooms': lambda qs: [
            {'id': row.id, 'label': row.roomNumber or 'N/A', 'meta': _hostel_room_meta(row)}
            for row in qs.select_related('buildingID', 'roomTypeID').order_by('buildingID__buildingCode', 'roomNumber', 'id')[:limit]
        ],
        'copy_hostel_beds': lambda qs: [
            {'id': row.id, 'label': row.bedNumber or 'N/A', 'meta': row.roomID.roomNumber if row.roomID else 'N/A'}
            for row in qs.select_related('roomID').order_by('roomID__roomNumber', 'bedNumber', 'id')[:limit]
        ],
        'copy_hostel_fee_mappings': lambda qs: [
            {'id': row.id, 'label': _hostel_fee_mapping_label(row), 'meta': str(row.monthlyFee)}
            for row in qs.select_related('buildingID', 'roomTypeID', 'roomID').order_by('buildingID__buildingCode', 'roomTypeID__name', 'roomID__roomNumber', 'id')[:limit]
        ],
        'copy_library_categories': lambda qs: [
            {'id': row.id, 'label': row.name or 'N/A', 'meta': row.code or ''}
            for row in qs.order_by('name', 'id')[:limit]
        ],
        'copy_library_authors': lambda qs: [
            {'id': row.id, 'label': row.name or 'N/A', 'meta': row.country or ''}
            for row in qs.order_by('name', 'id')[:limit]
        ],
        'copy_library_publishers': lambda qs: [
            {'id': row.id, 'label': row.name or 'N/A', 'meta': row.phoneNumber or row.email or ''}
            for row in qs.order_by('name', 'id')[:limit]
        ],
        'copy_library_books': lambda qs: [
            {'id': row.id, 'label': row.title or 'N/A', 'meta': row.isbn or row.shelfLocation or ''}
            for row in qs.order_by('title', 'id')[:limit]
        ],
        'copy_library_card_designs': lambda qs: [
            {'id': row.id, 'label': row.name or 'Library Card Design', 'meta': 'Active' if row.isActive else 'Inactive'}
            for row in qs.order_by('name', 'id')[:limit]
        ],
        'copy_library_settings': lambda qs: [
            {'id': row.id, 'label': 'Library Settings', 'meta': f'{row.defaultIssueDays} issue days'}
            for row in qs.order_by('id')[:limit]
        ],
        'copy_inventory_categories': lambda qs: [
            {'id': row.id, 'label': row.name or 'N/A', 'meta': ''}
            for row in qs.order_by('name', 'id')[:limit]
        ],
        'copy_inventory_suppliers': lambda qs: [
            {'id': row.id, 'label': row.companyName or 'N/A', 'meta': row.phone or row.email or ''}
            for row in qs.order_by('companyName', 'id')[:limit]
        ],
        'copy_inventory_items': lambda qs: [
            {'id': row.id, 'label': row.name or 'N/A', 'meta': f'{row.itemType} | {row.standardUnit}'}
            for row in qs.order_by('categoryID__name', 'name', 'id')[:limit]
        ],
        'copy_students': lambda qs: [
            {'id': row.id, 'label': row.name or 'N/A', 'meta': _class_label(row.standardID)}
            for row in qs.select_related('standardID').order_by('name', 'id')[:limit]
        ],
        'copy_parents': lambda qs: [
            {'id': row.id, 'label': row.fatherName or row.motherName or 'N/A', 'meta': row.phoneNumber or ''}
            for row in qs.order_by('fatherName', 'id')[:limit]
        ],
    }
    handler = handlers.get(option_key, lambda qs: [])
    return with_selection(handler(queryset))


def _teacher_subject_meta(row):
    assigned = getattr(row, 'assignedSubjectID', None)
    if not assigned:
        return row.subjectBranch or ''
    class_name = _class_label(getattr(assigned, 'standardID', None))
    subject_name = assigned.subjectID.name if getattr(assigned, 'subjectID', None) else 'N/A'
    branch = f' | {row.subjectBranch}' if row.subjectBranch else ''
    return f'{class_name} -> {subject_name}{branch}'


def _component_rule_label(row):
    exam_name = row.examID.examID.name if getattr(row, 'examID', None) and getattr(row.examID, 'examID', None) else 'N/A'
    subject_name = row.subjectID.subjectID.name if getattr(row, 'subjectID', None) and getattr(row.subjectID, 'subjectID', None) else 'N/A'
    component_name = row.componentTypeID.name if getattr(row, 'componentTypeID', None) else 'N/A'
    return f'{exam_name} / {subject_name} / {component_name}'


def _pass_policy_label(row):
    return row.examID.examID.name if getattr(row, 'examID', None) and getattr(row.examID, 'examID', None) else 'N/A'


def _date_range_label(start_date, end_date):
    start_label = start_date.isoformat() if start_date else 'N/A'
    end_label = end_date.isoformat() if end_date else start_label
    return start_label if start_label == end_label else f'{start_label} to {end_label}'


def _timetable_period_meta(row):
    timetable = getattr(row, 'timetableID', None)
    class_label = _class_label(getattr(timetable, 'standardID', None)) if timetable else 'N/A'
    start_label = row.startTime.strftime('%H:%M') if row.startTime else 'N/A'
    end_label = row.endTime.strftime('%H:%M') if row.endTime else 'N/A'
    return f'{class_label} | {start_label}-{end_label}'


def _timetable_entry_label(row):
    period = getattr(row, 'periodID', None)
    period_name = period.name if period else 'Period'
    return f'{row.dayOfWeek} / {period_name}'


def _timetable_entry_meta(row):
    timetable = getattr(row, 'timetableID', None)
    class_label = _class_label(getattr(timetable, 'standardID', None)) if timetable else 'N/A'
    assigned = getattr(row, 'assignedSubjectID', None)
    subject = assigned.subjectID.name if assigned and assigned.subjectID else 'N/A'
    teacher = row.teacherID.name if getattr(row, 'teacherID', None) else 'N/A'
    return f'{class_label} | {subject} | {teacher}'


def _transport_fee_mapping_label(row):
    route = row.routeID.routeName if getattr(row, 'routeID', None) else 'Route'
    stop = row.stopID.stopName if getattr(row, 'stopID', None) else 'Route default'
    return f'{route} / {stop}'


def _hostel_room_meta(row):
    building = row.buildingID.buildingName if getattr(row, 'buildingID', None) else 'N/A'
    room_type = row.roomTypeID.name if getattr(row, 'roomTypeID', None) else 'N/A'
    return f'{building} | {room_type}'


def _hostel_fee_mapping_label(row):
    if getattr(row, 'roomID', None):
        return f'Room {row.roomID.roomNumber}'
    if getattr(row, 'roomTypeID', None):
        return f'Room Type {row.roomTypeID.name}'
    if getattr(row, 'buildingID', None):
        return f'Building {row.buildingID.buildingName}'
    return 'Hostel default'
