from datetime import date, time
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from financeApp.models import (
    ExpenseCategory,
    FeeHead,
    FinanceAccount,
    FinanceApprovalRule,
    FinanceConfiguration,
    FinanceParty,
    FinancePaymentMode,
)
from homeApp.models import SchoolDetail, SchoolOwner, SchoolSession
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


class Command(BaseCommand):
    help = 'Seed a two-session demo dataset for testing management/session-import/.'

    DEMO_SCHOOL_NAME = 'Session Import Demo School'

    def add_arguments(self, parser):
        parser.add_argument(
            '--keep-existing',
            action='store_true',
            help='Keep an existing demo school instead of recreating it.',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        keep_existing = bool(options['keep_existing'])
        existing = SchoolDetail.objects.filter(schoolName=self.DEMO_SCHOOL_NAME, isDeleted=False).first()
        if existing and keep_existing:
            self.stdout.write(self.style.WARNING(f'Demo school already exists: id={existing.id}'))
            return
        if existing:
            existing.delete()

        owner = SchoolOwner.objects.create(
            name='Session Import Demo Owner',
            email='session-import-demo@example.com',
            phoneNumber='9000000100',
            username='session_import_demo_owner',
            isActive=True,
            userGroup='Owner',
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        school = SchoolDetail.objects.create(
            ownerID=owner,
            schoolName=self.DEMO_SCHOOL_NAME,
            name=self.DEMO_SCHOOL_NAME,
            address='Demo Campus, Rollover Road',
            city='Bhubaneswar',
            state='Odisha',
            country='India',
            pinCode='751001',
            phoneNumber='9000000101',
            email='demo-school@example.com',
            website='https://demo.schoolstack.local',
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        school.owners.add(owner)

        source_session = SchoolSession.objects.create(
            schoolID=school,
            sessionYear='2030-2031',
            startDate=date(2030, 4, 1),
            endDate=date(2031, 3, 31),
            isCurrent=False,
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        target_session = SchoolSession.objects.create(
            schoolID=school,
            sessionYear='2031-2032',
            startDate=date(2031, 4, 1),
            endDate=date(2032, 3, 31),
            isCurrent=True,
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )

        context = self._seed_academic_setup(school, source_session)
        self._seed_operations_setup(school, source_session, context)

        self.stdout.write(self.style.SUCCESS('Session import demo data seeded.'))
        self.stdout.write(
            self.style.SUCCESS(
                f'SchoolID={school.id}, SourceSessionID={source_session.id}, TargetSessionID={target_session.id}'
            )
        )
        self.stdout.write(
            'Use these sessions in management/session-import/ or call preview_session_import() with the IDs above.'
        )

    def _seed_academic_setup(self, school, session):
        teacher_1 = TeacherDetail.objects.create(
            schoolID=school,
            sessionID=session,
            name='Asha Sharma',
            phoneNumber='9000000201',
            email='asha.teacher@example.com',
            employeeCode='T-DEMO-001',
            currentPosition='Class Teacher',
            staffType='Teaching',
            salary=Decimal('42000.00'),
            isActive=True,
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        teacher_2 = TeacherDetail.objects.create(
            schoolID=school,
            sessionID=session,
            name='Rahul Verma',
            phoneNumber='9000000202',
            email='rahul.teacher@example.com',
            employeeCode='T-DEMO-002',
            currentPosition='Science Faculty',
            staffType='Teaching',
            salary=Decimal('45000.00'),
            isActive=True,
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        class_5 = Standard.objects.create(
            schoolID=school,
            sessionID=session,
            name='Class 5',
            section='A',
            hasSection=True,
            startingRoll=1,
            endingRoll=60,
            classTeacher=teacher_1,
            classLocation='Block A - 105',
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        class_6 = Standard.objects.create(
            schoolID=school,
            sessionID=session,
            name='Class 6',
            section='A',
            hasSection=True,
            startingRoll=1,
            endingRoll=60,
            classTeacher=teacher_2,
            classLocation='Block A - 106',
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        math = Subjects.objects.create(
            schoolID=school,
            sessionID=session,
            name='Mathematics',
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        science = Subjects.objects.create(
            schoolID=school,
            sessionID=session,
            name='Science',
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        class_5_math = AssignSubjectsToClass.objects.create(
            schoolID=school,
            sessionID=session,
            standardID=class_5,
            subjectID=math,
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        class_5_science = AssignSubjectsToClass.objects.create(
            schoolID=school,
            sessionID=session,
            standardID=class_5,
            subjectID=science,
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        AssignSubjectsToTeacher.objects.create(
            schoolID=school,
            sessionID=session,
            assignedSubjectID=class_5_math,
            teacherID=teacher_1,
            subjectBranch='Main',
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        AssignSubjectsToTeacher.objects.create(
            schoolID=school,
            sessionID=session,
            assignedSubjectID=class_5_science,
            teacherID=teacher_2,
            subjectBranch='Lab',
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        parent = Parent.objects.create(
            schoolID=school,
            sessionID=session,
            fatherName='Sanjay Mehta',
            motherName='Priya Mehta',
            phoneNumber='9000000301',
            fatherPhone='9000000301',
            motherPhone='9000000302',
            familyType='Nuclear',
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        student = Student.objects.create(
            schoolID=school,
            sessionID=session,
            parentID=parent,
            standardID=class_5,
            roll=1,
            registrationCode='SID-DEMO-001',
            name='Aarav Mehta',
            dob=date(2020, 5, 12),
            gender='Male',
            phoneNumber='9000000301',
            dateOfJoining=date(2030, 4, 5),
            isActive=True,
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )

        exam = Exam.objects.create(
            schoolID=school,
            sessionID=session,
            name='Term 1',
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        assign_exam = AssignExamToClass.objects.create(
            schoolID=school,
            sessionID=session,
            standardID=class_5,
            examID=exam,
            fullMarks=100,
            passMarks=33,
            startDate=date(2030, 9, 10),
            endDate=date(2030, 9, 20),
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        ExamTimeTable.objects.create(
            schoolID=school,
            sessionID=session,
            standardID=class_5,
            examID=exam,
            subjectID=math,
            examDate=date(2030, 9, 12),
            startTime=time(9, 0),
            endTime=time(12, 0),
            roomNo='101',
            note='Demo timetable row',
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )

        component = ExamComponentType.objects.create(
            schoolID=school,
            sessionID=session,
            name='Written',
            code='WR',
            displayOrder=1,
            isScholastic=True,
            isActive=True,
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        grading_policy = GradingPolicy.objects.create(
            schoolID=school,
            sessionID=session,
            name='Default Grading',
            isDefault=True,
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        GradingBand.objects.create(
            schoolID=school,
            sessionID=session,
            policyID=grading_policy,
            minPercentage=90,
            maxPercentage=100,
            gradeLabel='A+',
            gradePoint=10,
            displayOrder=1,
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        PassPolicy.objects.create(
            schoolID=school,
            sessionID=session,
            examID=assign_exam,
            gradingPolicyID=grading_policy,
            overallPassMarks=33,
            requireComponentPass=False,
            requireSubjectPass=True,
            requireMandatoryComponents=True,
            resultComputationMode='percentage',
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        ExamSubjectComponentRule.objects.create(
            schoolID=school,
            sessionID=session,
            examID=assign_exam,
            subjectID=class_5_math,
            componentTypeID=component,
            maxMarks=100,
            passMarks=33,
            weightage=100,
            isMandatory=True,
            displayOrder=1,
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        CoScholasticArea.objects.create(
            schoolID=school,
            sessionID=session,
            name='Discipline',
            code='DISC',
            displayOrder=1,
            isActive=True,
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        LeaveType.objects.create(
            schoolID=school,
            sessionID=session,
            name='Casual Leave',
            code='CL',
            applicableFor='both',
            quotaDays=12,
            requiresApproval=True,
            isActive=True,
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        EventType.objects.create(
            schoolID=school,
            sessionID=session,
            name='Assembly',
            audience='both',
            description='Demo event type',
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        SchoolHoliday.objects.create(
            schoolID=school,
            sessionID=session,
            title='Founders Day',
            holidayType='general',
            appliesTo='both',
            startDate=date(2030, 8, 15),
            endDate=date(2030, 8, 15),
            description='Demo holiday',
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        StudentIdCardDesign.objects.create(
            schoolID=school,
            sessionID=session,
            name='Demo Student Card',
            isActive=True,
            headerConfig={'title': 'Demo School'},
            fieldsConfig=['name', 'class', 'roll'],
            styleConfig={'theme': 'classic'},
            footerConfig={'text': 'Valid for demo'},
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        timetable = SchoolTimetable.objects.create(
            schoolID=school,
            sessionID=session,
            standardID=class_5,
            name='Class 5 A Weekly Timetable',
            workingDays=['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'],
            status='published',
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        period = SchoolTimetablePeriod.objects.create(
            schoolID=school,
            sessionID=session,
            timetableID=timetable,
            name='Period 1',
            startTime=time(9, 0),
            endTime=time(9, 45),
            displayOrder=1,
            periodType='teaching',
            isBreak=False,
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        SchoolTimetableEntry.objects.create(
            schoolID=school,
            sessionID=session,
            timetableID=timetable,
            periodID=period,
            assignedSubjectID=class_5_math,
            teacherID=teacher_1,
            dayOfWeek='Monday',
            room='101',
            note='Demo timetable entry',
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )

        return {
            'teachers': [teacher_1, teacher_2],
            'classes': [class_5, class_6],
            'subjects': [math, science],
            'assign_subjects': [class_5_math, class_5_science],
            'parents': [parent],
            'students': [student],
        }

    def _seed_operations_setup(self, school, session, context):
        self._seed_finance(school, session, context)
        self._seed_transport(school, session)
        self._seed_hostel(school, session)
        self._seed_library(school, session)
        self._seed_inventory(school, session)

    def _seed_finance(self, school, session, context):
        cash = FinanceAccount.objects.create(
            schoolID=school,
            sessionID=session,
            accountCode='CASH',
            accountName='Cash Account',
            accountType='asset',
            openingBalance=Decimal('1000.00'),
            openingBalanceType='debit',
            isActive=True,
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        bank = FinanceAccount.objects.create(
            schoolID=school,
            sessionID=session,
            accountCode='BANK',
            accountName='Bank Account',
            accountType='asset',
            openingBalance=Decimal('5000.00'),
            openingBalanceType='debit',
            isActive=True,
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        income = FinanceAccount.objects.create(
            schoolID=school,
            sessionID=session,
            accountCode='FEE-INCOME',
            accountName='Fee Income',
            accountType='income',
            openingBalance=Decimal('0.00'),
            openingBalanceType='credit',
            isActive=True,
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        receivable = FinanceAccount.objects.create(
            schoolID=school,
            sessionID=session,
            accountCode='FEE-RECV',
            accountName='Fee Receivable',
            accountType='asset',
            openingBalance=Decimal('0.00'),
            openingBalanceType='debit',
            isActive=True,
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        expense = FinanceAccount.objects.create(
            schoolID=school,
            sessionID=session,
            accountCode='EXP-GEN',
            accountName='General Expense',
            accountType='expense',
            openingBalance=Decimal('0.00'),
            openingBalanceType='debit',
            isActive=True,
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        FinanceParty.objects.create(
            schoolID=school,
            sessionID=session,
            partyType='vendor',
            displayName='Demo Stationery Vendor',
            phoneNumber='9000000401',
            email='vendor@example.com',
            isActive=True,
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        FinanceParty.objects.create(
            schoolID=school,
            sessionID=session,
            partyType='student',
            studentID=context['students'][0],
            parentID=context['parents'][0],
            displayName=context['students'][0].name,
            phoneNumber=context['students'][0].phoneNumber,
            isActive=True,
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        FinancePaymentMode.objects.create(
            schoolID=school,
            sessionID=session,
            code='CASH',
            name='Cash',
            modeType='cash',
            linkedAccountID=cash,
            isActive=True,
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        FinanceConfiguration.objects.create(
            schoolID=school,
            sessionID=session,
            receiptTitle='Demo Receipt',
            receiptFooterNote='Demo footer note',
            defaultCashAccountID=cash,
            defaultBankAccountID=bank,
            receiptPrefix='DRC',
            voucherPrefix='DEX',
            refundPrefix='DRF',
            transactionPrefix='DTX',
            payrollPrefix='DPY',
            sequencePadding=4,
            includeDateSegment=True,
            lastReceiptSequence=25,
            lastVoucherSequence=8,
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        FeeHead.objects.create(
            schoolID=school,
            sessionID=session,
            code='TUITION',
            name='Tuition Fee',
            category='tuition',
            defaultAmount=Decimal('1500.00'),
            isRecurring=True,
            recurrenceType='monthly',
            incomeAccountID=income,
            receivableAccountID=receivable,
            displayOrder=1,
            isActive=True,
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        ExpenseCategory.objects.create(
            schoolID=school,
            sessionID=session,
            code='STATIONERY',
            name='Stationery',
            expenseAccountID=expense,
            isActive=True,
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        FinanceApprovalRule.objects.create(
            schoolID=school,
            sessionID=session,
            ruleName='Expense Approval Above 5000',
            documentType='expense_voucher',
            minAmount=Decimal('5000.00'),
            maxAmount=None,
            approvalMode='approval_required',
            priority=1,
            isActive=True,
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )

    def _seed_transport(self, school, session):
        route = TransportRoute.objects.create(
            schoolID=school,
            sessionID=session,
            routeCode='R1',
            routeName='City Route',
            startPoint='Campus',
            endPoint='City Center',
            isActive=True,
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        stop = TransportStop.objects.create(
            schoolID=school,
            sessionID=session,
            routeID=route,
            stopName='Main Square',
            pickupTime=time(7, 45),
            dropTime=time(15, 45),
            monthlyFee=Decimal('800.00'),
            displayOrder=1,
            isActive=True,
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        driver = TransportDriver.objects.create(
            schoolID=school,
            sessionID=session,
            name='Demo Driver',
            phoneNumber='9000000501',
            licenseNumber='DL-DEMO-001',
            isActive=True,
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        TransportVehicle.objects.create(
            schoolID=school,
            sessionID=session,
            vehicleNumber='OD-00-DEMO-1',
            vehicleType='bus',
            capacity=40,
            driverID=driver,
            routeID=route,
            isActive=True,
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        TransportFeeMapping.objects.create(
            schoolID=school,
            sessionID=session,
            routeID=route,
            stopID=stop,
            assigneeType='student',
            feeMode='student_fee',
            monthlyFee=Decimal('800.00'),
            isActive=True,
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )

    def _seed_hostel(self, school, session):
        building = HostelBuilding.objects.create(
            schoolID=school,
            sessionID=session,
            buildingCode='HB1',
            buildingName='Demo Hostel',
            wardenName='Demo Warden',
            wardenPhone='9000000601',
            isActive=True,
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        floor = HostelFloor.objects.create(
            schoolID=school,
            sessionID=session,
            buildingID=building,
            floorName='First Floor',
            displayOrder=1,
            isActive=True,
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        room_type = HostelRoomType.objects.create(
            schoolID=school,
            sessionID=session,
            name='Double Sharing',
            capacity=2,
            defaultMonthlyFee=Decimal('2500.00'),
            isActive=True,
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        room = HostelRoom.objects.create(
            schoolID=school,
            sessionID=session,
            buildingID=building,
            floorID=floor,
            roomTypeID=room_type,
            roomNumber='101',
            capacity=2,
            monthlyFee=Decimal('2500.00'),
            isActive=True,
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        HostelBed.objects.create(
            schoolID=school,
            sessionID=session,
            roomID=room,
            bedNumber='101-A',
            status='available',
            isActive=True,
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        HostelFeeMapping.objects.create(
            schoolID=school,
            sessionID=session,
            buildingID=building,
            roomTypeID=room_type,
            roomID=room,
            monthlyFee=Decimal('2500.00'),
            isActive=True,
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )

    def _seed_library(self, school, session):
        category = LibraryCategory.objects.create(
            schoolID=school,
            sessionID=session,
            name='Reference',
            code='REF',
            isActive=True,
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        author = LibraryAuthor.objects.create(
            schoolID=school,
            sessionID=session,
            name='Demo Author',
            country='India',
            isActive=True,
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        publisher = LibraryPublisher.objects.create(
            schoolID=school,
            sessionID=session,
            name='Demo Publisher',
            phoneNumber='9000000701',
            isActive=True,
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        book = LibraryBook.objects.create(
            schoolID=school,
            sessionID=session,
            title='Demo Science Companion',
            isbn='DEMO-ISBN-001',
            category=category,
            publisher=publisher,
            language='English',
            publicationYear=2030,
            shelfLocation='A1',
            isActive=True,
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        LibraryBookAuthor.objects.create(book=book, author=author)
        LibraryMemberCardDesign.objects.create(
            schoolID=school,
            sessionID=session,
            name='Demo Library Card',
            isActive=True,
            headerConfig={'title': 'Library'},
            fieldsConfig=['memberCode', 'name'],
            styleConfig={'theme': 'clean'},
            footerConfig={'text': 'Demo'},
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        LibrarySetting.objects.create(
            schoolID=school,
            sessionID=session,
            defaultIssueDays=14,
            finePerDay=Decimal('2.00'),
            graceDays=1,
            maxRenewals=2,
            defaultMaxBooksAllowed=3,
            reservationExpiryDays=3,
            lostBookFine=Decimal('500.00'),
            damagedBookFine=Decimal('200.00'),
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )

    def _seed_inventory(self, school, session):
        category = InventoryCategory.objects.create(
            schoolID=school,
            sessionID=session,
            name='Lab Supplies',
            description='Demo inventory category',
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        InventorySupplier.objects.create(
            schoolID=school,
            sessionID=session,
            companyName='Demo Lab Supplier',
            contactName='Supplier Contact',
            phone='9000000801',
            email='supplier@example.com',
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
        InventoryItem.objects.create(
            schoolID=school,
            sessionID=session,
            categoryID=category,
            name='Microscope Slides',
            itemType='consumable',
            sku='LAB-SLIDE',
            standardUnit='box',
            minStockAlertLevel=5,
            currentStockQty=30,
            lastEditedBy='seed_session_import_demo',
            isDeleted=False,
        )
