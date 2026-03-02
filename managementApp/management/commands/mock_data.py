import random
from datetime import date, datetime, time, timedelta

from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from homeApp.models import SchoolDetail, SchoolOwner, SchoolSession, SchoolSocialLink
from managementApp.models import (
    AssignExamToClass,
    AssignSubjectsToClass,
    AssignSubjectsToTeacher,
    Event,
    EventType,
    Exam,
    ExamTimeTable,
    MarkOfStudentsByExam,
    Parent,
    Standard,
    Student,
    StudentAttendance,
    StudentFee,
    StudentIdCardRecord,
    Subjects,
    TeacherAttendance,
    TeacherDetail,
)


class Command(BaseCommand):
    help = "Create linked mock data across school models (default size: 60 students)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--size",
            type=int,
            default=60,
            help="Number of students to generate (default: 60).",
        )
        parser.add_argument(
            "--seed",
            type=int,
            default=42,
            help="Random seed for deterministic mock data.",
        )
        parser.add_argument(
            "--keep-existing",
            action="store_true",
            help="Keep previously generated mock_* records (otherwise clean and recreate).",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        size = max(10, min(200, int(options["size"])))
        seed = int(options["seed"])
        keep_existing = bool(options["keep_existing"])

        random.seed(seed)
        self.stdout.write(self.style.NOTICE(f"Generating mock dataset (size={size}, seed={seed}) ..."))

        if not keep_existing:
            self._cleanup_previous_mock_data()

        groups = self._ensure_groups()
        owner_user, owner, school, current_session = self._create_school_setup(groups)
        sessions = self._create_sessions(school, current_session)
        teachers = self._create_teachers(size, school, current_session, groups["Teaching"])
        standards = self._create_standards(size, school, current_session, teachers)
        subjects = self._create_subjects(school, current_session)
        class_subject_map = self._create_subject_assignments_to_class(school, current_session, standards, subjects)
        self._create_subject_assignments_to_teacher(school, current_session, class_subject_map, teachers)
        parents = self._create_parents(size, school, current_session)
        students = self._create_students(size, school, current_session, standards, parents, groups["Student"])
        exams = self._create_exams(school, current_session)
        assigned_exams = self._create_assign_exams_to_class(school, current_session, standards, exams)
        self._create_exam_timetable(school, current_session, assigned_exams, subjects)
        self._create_student_attendance(school, current_session, students, class_subject_map)
        self._create_teacher_attendance(school, current_session, teachers)
        self._create_student_fee(school, current_session, students)
        self._create_student_marks(school, current_session, students, assigned_exams, class_subject_map)
        event_types = self._create_event_types(school, current_session)
        self._create_events(school, current_session, event_types)
        self._create_id_card_records(school, current_session, students)

        self.stdout.write(self.style.SUCCESS("Mock data generation completed."))
        self.stdout.write(
            self.style.SUCCESS(
                f"Owner={owner_user.username}, sessions={len(sessions)}, teachers={len(teachers)}, "
                f"classes={len(standards)}, subjects={len(subjects)}, parents={len(parents)}, students={len(students)}"
            )
        )

    def _cleanup_previous_mock_data(self):
        school_qs = SchoolDetail.objects.filter(schoolName__startswith="Mock School")
        school_count = school_qs.count()
        school_qs.delete()

        owner_qs = SchoolOwner.objects.filter(username__startswith="mock_")
        owner_count = owner_qs.count()
        owner_qs.delete()

        user_count, _ = User.objects.filter(username__startswith="mock_").delete()
        self.stdout.write(
            self.style.WARNING(
                f"Deleted previous mock data: schools={school_count}, owners={owner_count}, users={user_count}"
            )
        )

    def _ensure_groups(self):
        group_names = ["Owner", "Admin", "Teaching", "Student"]
        groups = {}
        for name in group_names:
            group, _ = Group.objects.get_or_create(name=name)
            groups[name] = group
        return groups

    def _create_school_setup(self, groups):
        owner_user, _ = User.objects.get_or_create(username="mock_owner")
        owner_user.set_password("mock@123")
        owner_user.email = "mock.owner@example.com"
        owner_user.is_staff = True
        owner_user.save()
        owner_user.groups.add(groups["Owner"], groups["Admin"])

        owner = SchoolOwner.objects.create(
            name="Mock Owner",
            email=owner_user.email,
            password="mock@123",
            phoneNumber="9000000000",
            username=owner_user.username,
            userID=owner_user,
            isActive=True,
            userGroup="Owner",
            lastEditedBy="mock_data",
            isDeleted=False,
        )

        school = SchoolDetail.objects.create(
            ownerID=owner,
            schoolName="Mock School Academy",
            name="Mock School Academy",
            address="123 Mock Street, Model Town",
            city="Bhubaneswar",
            state="Odisha",
            country="India",
            pinCode="751001",
            phoneNumber="9111111111",
            email="school@example.com",
            website="https://mock-school.example.com",
            lastEditedBy="mock_data",
            isDeleted=False,
        )

        current_session = SchoolSession.objects.create(
            schoolID=school,
            sessionYear="2025-2026",
            isCurrent=True,
            lastEditedBy="mock_data",
            isDeleted=False,
        )
        SchoolSocialLink.objects.create(
            schoolID=school,
            facebook="https://facebook.com/mockschool",
            instagram="https://instagram.com/mockschool",
            twitter="https://twitter.com/mockschool",
            googlePlus="N/A",
            lastEditedBy="mock_data",
            isDeleted=False,
        )
        return owner_user, owner, school, current_session

    def _create_sessions(self, school, current_session):
        sessions = [current_session]
        prev_year = SchoolSession.objects.create(
            schoolID=school,
            sessionYear="2024-2025",
            isCurrent=False,
            lastEditedBy="mock_data",
            isDeleted=False,
        )
        next_year = SchoolSession.objects.create(
            schoolID=school,
            sessionYear="2026-2027",
            isCurrent=False,
            lastEditedBy="mock_data",
            isDeleted=False,
        )
        sessions.extend([prev_year, next_year])
        return sessions

    def _create_teachers(self, size, school, session, teaching_group):
        teacher_count = max(8, size // 8)
        teachers = []
        for idx in range(teacher_count):
            username = f"mock_teacher_{idx + 1:02d}"
            user = User.objects.create_user(
                username=username,
                password="mock@123",
                email=f"{username}@example.com",
                first_name=f"Teacher{idx + 1}",
            )
            user.groups.add(teaching_group)

            teacher = TeacherDetail.objects.create(
                sessionID=session,
                schoolID=school,
                name=f"Teacher {idx + 1}",
                dob=date(1985 + (idx % 10), (idx % 12) + 1, ((idx * 2) % 28) + 1),
                gender="Male" if idx % 2 == 0 else "Female",
                bloodGroup=random.choice(["A+", "B+", "AB+", "O+", "O-"]),
                presentAddress=f"Teacher Address {idx + 1}",
                presentCity="Bhubaneswar",
                presentState="Odisha",
                presentCountry="India",
                phoneNumber=f"91{7000000000 + idx}",
                email=user.email,
                username=username,
                password="mock@123",
                userID=user,
                dateOfJoining=date(2020 + (idx % 5), ((idx + 2) % 12) + 1, ((idx + 5) % 28) + 1),
                currentPosition="Teacher",
                staffType="Teaching",
                employeeCode=f"TCHR{1000 + idx}",
                qualification=random.choice(["B.Ed", "M.Ed", "M.Sc", "B.Sc"]),
                salary=25000 + (idx * 1200),
                lastEditedBy="mock_data",
                isDeleted=False,
                isActive="Yes",
            )
            teachers.append(teacher)
        return teachers

    def _create_standards(self, size, school, session, teachers):
        class_count = max(5, min(10, size // 10))
        standards = []
        for idx in range(class_count):
            standards.append(
                Standard.objects.create(
                    name=f"Class {idx + 1}",
                    classLocation=f"Block-{(idx % 3) + 1}",
                    schoolID=school,
                    sessionID=session,
                    hasSection="Yes",
                    section=random.choice(["A", "B"]),
                    startingRoll="1",
                    endingRoll="60",
                    classTeacher=teachers[idx % len(teachers)],
                    lastEditedBy="mock_data",
                    isDeleted=False,
                )
            )
        return standards

    def _create_subjects(self, school, session):
        subject_names = [
            "English",
            "Mathematics",
            "Science",
            "Social Science",
            "Hindi",
            "Computer",
            "General Knowledge",
            "Sanskrit",
            "Physics",
            "Chemistry",
        ]
        subjects = []
        for name in subject_names:
            subjects.append(
                Subjects.objects.create(
                    name=name,
                    schoolID=school,
                    sessionID=session,
                    lastEditedBy="mock_data",
                    isDeleted=False,
                )
            )
        return subjects

    def _create_subject_assignments_to_class(self, school, session, standards, subjects):
        class_subject_map = {}
        for std in standards:
            chosen = random.sample(subjects, k=min(6, len(subjects)))
            assigned_rows = []
            for subject in chosen:
                row = AssignSubjectsToClass.objects.create(
                    standardID=std,
                    subjectID=subject,
                    schoolID=school,
                    sessionID=session,
                    lastEditedBy="mock_data",
                    isDeleted=False,
                )
                assigned_rows.append(row)
            class_subject_map[std.id] = assigned_rows
        return class_subject_map

    def _create_subject_assignments_to_teacher(self, school, session, class_subject_map, teachers):
        all_class_subject_rows = [row for rows in class_subject_map.values() for row in rows]
        for idx, class_subject in enumerate(all_class_subject_rows):
            AssignSubjectsToTeacher.objects.create(
                assignedSubjectID=class_subject,
                teacherID=teachers[idx % len(teachers)],
                schoolID=school,
                sessionID=session,
                subjectBranch=random.choice(["Morning", "Regular", "Afternoon"]),
                lastEditedBy="mock_data",
                isDeleted=False,
            )

    def _create_parents(self, size, school, session):
        parent_count = max(30, size // 2)
        parents = []
        for idx in range(parent_count):
            father_name = f"Father {idx + 1}"
            mother_name = f"Mother {idx + 1}"
            parents.append(
                Parent.objects.create(
                    fatherName=father_name,
                    motherName=mother_name,
                    email=f"parent{idx + 1}@example.com",
                    phoneNumber=f"98{60000000 + idx:08d}",
                    profession=random.choice(["Business", "Service", "Farmer", "Teacher"]),
                    schoolID=school,
                    sessionID=session,
                    fatherOccupation="Business",
                    motherOccupation="Homemaker",
                    fatherAddress=f"Father Address {idx + 1}",
                    motherAddress=f"Mother Address {idx + 1}",
                    fatherPhone=f"97{50000000 + idx:08d}",
                    motherPhone=f"96{40000000 + idx:08d}",
                    guardianName=f"Guardian {idx + 1}",
                    guardianOccupation="Service",
                    guardianPhone=f"95{30000000 + idx:08d}",
                    fatherEmail=f"father{idx + 1}@example.com",
                    motherEmail=f"mother{idx + 1}@example.com",
                    familyType=random.choice(["Single Parent", "Nuclear Family", "Joint Family"]),
                    totalFamilyMembers=random.randint(3, 8),
                    annualIncome=float(random.randint(250000, 1200000)),
                    lastEditedBy="mock_data",
                    isDeleted=False,
                )
            )
        return parents

    def _create_students(self, size, school, session, standards, parents, student_group):
        students = []
        for idx in range(size):
            username = f"mock_student_{idx + 1:03d}"
            user = User.objects.create_user(
                username=username,
                password="mock@123",
                email=f"{username}@example.com",
                first_name=f"Student{idx + 1}",
            )
            user.groups.add(student_group)

            standard = standards[idx % len(standards)]
            parent = parents[idx % len(parents)]

            student = Student.objects.create(
                sessionID=session,
                schoolID=school,
                parentID=parent,
                standardID=standard,
                roll=str((idx % 60) + 1),
                registrationCode=f"REG{2026}{idx + 1:04d}",
                name=f"Student {idx + 1}",
                dob=date(2010 + (idx % 8), ((idx % 12) + 1), ((idx % 28) + 1)),
                gender="Male" if idx % 2 == 0 else "Female",
                bloodGroup=random.choice(["A+", "B+", "AB+", "O+", "O-"]),
                presentAddress=f"Student Address {idx + 1}",
                presentCity="Bhubaneswar",
                presentState="Odisha",
                presentCountry="India",
                phoneNumber=f"90{idx + 10000000:08d}",
                email=user.email,
                username=username,
                password="mock@123",
                userID=user,
                dateOfJoining=date(2024, 4, 1),
                additionalDetails="Mock student profile",
                isActive="Yes",
                admissionFee=5000.0,
                tuitionFee=2500.0 + ((idx % 5) * 200),
                idMark="Mole on left hand",
                caste=random.choice(["GEN", "OBC", "SC", "ST"]),
                languageKnown="English, Hindi",
                religion=random.choice(["Hindu", "Muslim", "Christian"]),
                motherTongue=random.choice(["Hindi", "Odia", "Bengali"]),
                siblingsCount=random.randint(0, 3),
                januaryTuitionFee=2500.0,
                miscFee=400.0,
                totalFee=2900.0,
                lastEditedBy="mock_data",
                isDeleted=False,
            )
            students.append(student)
        return students

    def _create_exams(self, school, session):
        exams = []
        for exam_name in ["Unit Test 1", "Half Yearly", "Unit Test 2", "Annual Exam"]:
            exams.append(
                Exam.objects.create(
                    name=exam_name,
                    schoolID=school,
                    sessionID=session,
                    lastEditedBy="mock_data",
                    isDeleted=False,
                )
            )
        return exams

    def _create_assign_exams_to_class(self, school, session, standards, exams):
        assigned_exams = []
        for std in standards:
            for exam in exams[:3]:
                start = date(2026, random.randint(1, 10), random.randint(1, 20))
                assigned_exams.append(
                    AssignExamToClass.objects.create(
                        standardID=std,
                        examID=exam,
                        fullMarks=100,
                        passMarks=33,
                        startDate=start,
                        endDate=start + timedelta(days=7),
                        schoolID=school,
                        sessionID=session,
                        lastEditedBy="mock_data",
                        isDeleted=False,
                    )
                )
        return assigned_exams

    def _create_exam_timetable(self, school, session, assigned_exams, subjects):
        for assigned_exam in assigned_exams:
            selected_subjects = random.sample(subjects, k=min(4, len(subjects)))
            base_date = assigned_exam.startDate or date.today()
            for idx, subject in enumerate(selected_subjects):
                start_t = time(9 + (idx % 3), 0)
                end_t = time(min(12 + (idx % 3), 17), 0)
                ExamTimeTable.objects.create(
                    schoolID=school,
                    sessionID=session,
                    standardID=assigned_exam.standardID,
                    examID=assigned_exam.examID,
                    subjectID=subject,
                    examDate=base_date + timedelta(days=idx),
                    startTime=start_t,
                    endTime=end_t,
                    roomNo=f"R-{(idx % 6) + 1}",
                    note="Mock timetable",
                    lastEditedBy="mock_data",
                    isDeleted=False,
                )

    def _create_student_attendance(self, school, session, students, class_subject_map):
        start_day = timezone.now() - timedelta(days=45)
        for student_idx, student in enumerate(students):
            class_subjects = class_subject_map.get(student.standardID_id, [])
            if not class_subjects:
                continue
            for day_offset in range(30):
                day = start_day + timedelta(days=day_offset)
                if day.weekday() >= 6:
                    continue
                assigned_subject = class_subjects[(student_idx + day_offset) % len(class_subjects)]
                is_present = (student_idx + day_offset) % 7 != 0
                StudentAttendance.objects.create(
                    isPresent=is_present,
                    isHoliday=False,
                    bySubject=True,
                    studentID=student,
                    standardID=student.standardID,
                    subjectID=assigned_subject.subjectID,
                    sessionID=session,
                    schoolID=school,
                    attendanceDate=day.replace(hour=9, minute=0, second=0, microsecond=0),
                    absentReason="" if is_present else "Sick leave",
                    lastEditedBy="mock_data",
                    isDeleted=False,
                )

    def _create_teacher_attendance(self, school, session, teachers):
        start_day = timezone.now() - timedelta(days=30)
        for teacher_idx, teacher in enumerate(teachers):
            for day_offset in range(20):
                day = start_day + timedelta(days=day_offset)
                if day.weekday() >= 6:
                    continue
                is_present = (teacher_idx + day_offset) % 9 != 0
                TeacherAttendance.objects.create(
                    isPresent=is_present,
                    isHoliday=False,
                    teacherID=teacher,
                    sessionID=session,
                    schoolID=school,
                    attendanceDate=day.replace(hour=8, minute=45, second=0, microsecond=0),
                    absentReason="" if is_present else "Personal work",
                    lastEditedBy="mock_data",
                    isDeleted=False,
                )

    def _create_student_fee(self, school, session, students):
        months = [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ]
        for idx, student in enumerate(students):
            for month_idx, month_name in enumerate(months[:6]):
                is_paid = (idx + month_idx) % 4 != 0
                StudentFee.objects.create(
                    schoolID=school,
                    sessionID=session,
                    studentID=student,
                    standardID=student.standardID,
                    month=month_name,
                    note="Paid via online" if is_paid else "Pending payment",
                    amount=2500 + (idx % 4) * 200,
                    payDate=date(2026, month_idx + 1, 10) if is_paid else None,
                    isPaid=is_paid,
                    lastEditedBy="mock_data",
                    isDeleted=False,
                )

    def _create_student_marks(self, school, session, students, assigned_exams, class_subject_map):
        exam_by_class = {}
        for assigned in assigned_exams:
            exam_by_class.setdefault(assigned.standardID_id, []).append(assigned)

        for idx, student in enumerate(students):
            class_exams = exam_by_class.get(student.standardID_id, [])
            class_subjects = class_subject_map.get(student.standardID_id, [])
            if not class_exams or not class_subjects:
                continue

            chosen_exams = class_exams[:2]
            chosen_subjects = class_subjects[:3]
            for exam in chosen_exams:
                for assigned_subject in chosen_subjects:
                    mark = float(random.randint(28, 96))
                    MarkOfStudentsByExam.objects.create(
                        schoolID=school,
                        sessionID=session,
                        examID=exam,
                        studentID=student,
                        standardID=student.standardID,
                        subjectID=assigned_subject,
                        mark=mark,
                        note="Good" if mark >= 60 else "Needs improvement",
                        lastEditedBy="mock_data",
                        isDeleted=False,
                    )

    def _create_event_types(self, school, session):
        specs = [
            ("General Notice", "general"),
            ("Teacher Meeting", "teacherapp"),
            ("Student Activity", "studentapp"),
            ("Management Review", "managementapp"),
            ("All Hands", "all_apps"),
        ]
        event_types = []
        for name, audience in specs:
            event_types.append(
                EventType.objects.create(
                    schoolID=school,
                    sessionID=session,
                    name=name,
                    audience=audience,
                    description=f"Mock {name} events",
                    lastEditedBy="mock_data",
                    isDeleted=False,
                )
            )
        return event_types

    def _create_events(self, school, session, event_types):
        base_date = date.today()
        for idx in range(20):
            event_type = event_types[idx % len(event_types)]
            start = base_date + timedelta(days=idx * 2)
            Event.objects.create(
                schoolID=school,
                sessionID=session,
                eventID=event_type,
                title=f"Mock Event {idx + 1}",
                message=f"This is mock event {idx + 1} for {event_type.get_audience_display()}.",
                startDate=start,
                endDate=start + timedelta(days=(idx % 2)),
                isDeleted=False,
            )

    def _create_id_card_records(self, school, session, students):
        for idx, student in enumerate(students):
            action = "issue" if idx % 5 == 0 else ("reissue" if idx % 7 == 0 else "print")
            StudentIdCardRecord.objects.create(
                studentID=student,
                schoolID=school,
                sessionID=session,
                actionType=action,
                validTill=date(2026, 12, 31),
                remark="Generated by mock_data command",
                lastEditedBy="mock_data",
                isDeleted=False,
            )
