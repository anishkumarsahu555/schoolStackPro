from django.contrib import admin

from hostelApp.models import (
    HostelAdmission,
    HostelAssignment,
    HostelBed,
    HostelBuilding,
    HostelFeeMapping,
    HostelFeeRecord,
    HostelFloor,
    HostelRoom,
    HostelRoomType,
)


@admin.register(HostelBuilding)
class HostelBuildingAdmin(admin.ModelAdmin):
    list_display = ('buildingCode', 'buildingName', 'wardenName', 'schoolID', 'sessionID', 'isActive', 'isDeleted')
    list_filter = ('schoolID', 'sessionID', 'isActive', 'isDeleted')
    search_fields = ('=id', 'buildingCode', 'buildingName', 'wardenName')


@admin.register(HostelFloor)
class HostelFloorAdmin(admin.ModelAdmin):
    list_display = ('floorName', 'buildingID', 'displayOrder', 'isActive', 'isDeleted')
    list_filter = ('schoolID', 'sessionID', 'isActive', 'isDeleted')
    search_fields = ('=id', 'floorName', 'buildingID__buildingName')


@admin.register(HostelRoomType)
class HostelRoomTypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'capacity', 'defaultMonthlyFee', 'isActive', 'isDeleted')
    list_filter = ('schoolID', 'sessionID', 'isActive', 'isDeleted')
    search_fields = ('=id', 'name')


@admin.register(HostelRoom)
class HostelRoomAdmin(admin.ModelAdmin):
    list_display = ('roomNumber', 'buildingID', 'floorID', 'roomTypeID', 'capacity', 'monthlyFee', 'isActive', 'isDeleted')
    list_filter = ('schoolID', 'sessionID', 'isActive', 'isDeleted')
    search_fields = ('=id', 'roomNumber', 'buildingID__buildingName')


@admin.register(HostelBed)
class HostelBedAdmin(admin.ModelAdmin):
    list_display = ('bedNumber', 'roomID', 'status', 'isActive', 'isDeleted')
    list_filter = ('schoolID', 'sessionID', 'status', 'isActive', 'isDeleted')
    search_fields = ('=id', 'bedNumber', 'roomID__roomNumber')


@admin.register(HostelAdmission)
class HostelAdmissionAdmin(admin.ModelAdmin):
    list_display = ('applicationNo', 'residentType', 'studentID', 'teacherID', 'applicationDate', 'status', 'admissionFee', 'isActive', 'isDeleted')
    list_filter = ('schoolID', 'sessionID', 'residentType', 'status', 'isActive', 'isDeleted')
    search_fields = ('=id', 'applicationNo', 'studentID__name', 'studentID__registrationCode', 'teacherID__name', 'teacherID__employeeCode')


@admin.register(HostelAssignment)
class HostelAssignmentAdmin(admin.ModelAdmin):
    list_display = ('residentType', 'studentID', 'teacherID', 'buildingID', 'roomID', 'bedID', 'monthlyFee', 'feeMode', 'isActive', 'isDeleted')
    list_filter = ('schoolID', 'sessionID', 'residentType', 'feeMode', 'isActive', 'isDeleted')
    search_fields = ('=id', 'studentID__name', 'teacherID__name', 'roomID__roomNumber', 'bedID__bedNumber')


@admin.register(HostelFeeMapping)
class HostelFeeMappingAdmin(admin.ModelAdmin):
    list_display = ('buildingID', 'roomTypeID', 'roomID', 'monthlyFee', 'effectiveFrom', 'effectiveTo', 'isActive', 'isDeleted')
    list_filter = ('schoolID', 'sessionID', 'isActive', 'isDeleted')
    search_fields = ('=id', 'buildingID__buildingName', 'roomTypeID__name', 'roomID__roomNumber')


@admin.register(HostelFeeRecord)
class HostelFeeRecordAdmin(admin.ModelAdmin):
    list_display = ('assignmentID', 'feeMonth', 'feeYear', 'netAmount', 'paidAmount', 'balanceAmount', 'status', 'isDeleted')
    list_filter = ('schoolID', 'sessionID', 'feeYear', 'feeMonth', 'status', 'isDeleted')
    search_fields = ('=id', 'assignmentID__studentID__name', 'assignmentID__teacherID__name', 'referenceNo')
