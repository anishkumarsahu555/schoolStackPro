from collections import defaultdict

from django.db.models import Q

from homeApp.session_utils import get_session_month_sequence
from managementApp.models import Student, StudentFee


def sync_session_fee_periods(*, session_obj, create_missing=False, dry_run=False, target_pairs=None):
    month_rows = get_session_month_sequence(session_obj)
    if not month_rows:
        return {
            'groups': 0,
            'updated': 0,
            'created': 0,
            'unchanged': 0,
            'missing': 0,
        }

    if target_pairs is None:
        raw_pairs = list(Student.objects.filter(
            sessionID_id=session_obj.pk,
            isDeleted=False,
        ).values_list('id', 'standardID_id'))
    else:
        raw_pairs = list(target_pairs)

    normalized_pairs = []
    seen_pairs = set()
    for student_id, standard_id in raw_pairs:
        if not student_id or not standard_id:
            continue
        key = (student_id, standard_id)
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        normalized_pairs.append(key)

    fee_qs = StudentFee.objects.filter(
        sessionID_id=session_obj.pk,
        isDeleted=False,
    )
    if normalized_pairs:
        pair_filter = Q()
        for student_id, standard_id in normalized_pairs:
            pair_filter |= Q(studentID_id=student_id, standardID_id=standard_id)
        fee_qs = fee_qs.filter(pair_filter)
    else:
        fee_qs = fee_qs.none()

    fee_rows = list(fee_qs.order_by(
        'studentID_id',
        'standardID_id',
        'periodStartDate',
        'feeYear',
        'feeMonth',
        'id',
    ))

    grouped = defaultdict(list)
    for row in fee_rows:
        grouped[(row.studentID_id, row.standardID_id)].append(row)
    for student_id, standard_id in normalized_pairs:
        grouped.setdefault((student_id, standard_id), [])

    summary = {
        'groups': len(grouped),
        'updated': 0,
        'created': 0,
        'unchanged': 0,
        'missing': 0,
    }

    for (student_id, standard_id), rows in grouped.items():
        by_year_month = {}
        by_month_name = defaultdict(list)

        for row in rows:
            if row.feeYear and row.feeMonth and (row.feeYear, row.feeMonth) not in by_year_month:
                by_year_month[(row.feeYear, row.feeMonth)] = row
            if row.month:
                by_month_name[row.month.strip().lower()].append(row)

        used_ids = set()

        for month_name, year_value, month_no, period_start, period_end in month_rows:
            selected = None

            key = (year_value, month_no)
            candidate = by_year_month.get(key)
            if candidate and candidate.id not in used_ids:
                selected = candidate

            if not selected:
                month_candidates = by_month_name.get(month_name.lower(), [])
                for candidate in month_candidates:
                    if candidate.id not in used_ids:
                        selected = candidate
                        break

            if not selected:
                for candidate in rows:
                    if candidate.id not in used_ids:
                        selected = candidate
                        break

            if not selected:
                if create_missing:
                    summary['created'] += 1
                    if not dry_run:
                        StudentFee.objects.create(
                            schoolID_id=session_obj.schoolID_id,
                            sessionID_id=session_obj.pk,
                            studentID_id=student_id,
                            standardID_id=standard_id,
                            month=month_name,
                            feeMonth=month_no,
                            feeYear=year_value,
                            periodStartDate=period_start,
                            periodEndDate=period_end,
                            dueDate=period_start,
                            isPaid=False,
                        )
                else:
                    summary['missing'] += 1
                continue

            used_ids.add(selected.id)

            changed_fields = []
            if selected.month != month_name:
                selected.month = month_name
                changed_fields.append('month')
            if selected.feeMonth != month_no:
                selected.feeMonth = month_no
                changed_fields.append('feeMonth')
            if selected.feeYear != year_value:
                selected.feeYear = year_value
                changed_fields.append('feeYear')
            if selected.periodStartDate != period_start:
                selected.periodStartDate = period_start
                changed_fields.append('periodStartDate')
            if selected.periodEndDate != period_end:
                selected.periodEndDate = period_end
                changed_fields.append('periodEndDate')
            if selected.dueDate != period_start:
                selected.dueDate = period_start
                changed_fields.append('dueDate')

            if changed_fields:
                summary['updated'] += 1
                if not dry_run:
                    selected.save(update_fields=changed_fields)
            else:
                summary['unchanged'] += 1

    return summary
