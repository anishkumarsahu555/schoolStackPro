from django.contrib import admin

from transportApp.models import (
    TransportAssignment,
    TransportDriver,
    TransportFeeMapping,
    TransportFeeRecord,
    TransportRoute,
    TransportStop,
    TransportVehicle,
)


@admin.register(TransportRoute)
class TransportRouteAdmin(admin.ModelAdmin):
    list_display = ('routeCode', 'routeName', 'schoolID', 'sessionID', 'isActive', 'isDeleted')
    list_filter = ('schoolID', 'sessionID', 'isActive', 'isDeleted')
    search_fields = ('=id', 'routeCode', 'routeName')


@admin.register(TransportStop)
class TransportStopAdmin(admin.ModelAdmin):
    list_display = ('stopName', 'routeID', 'monthlyFee', 'pickupTime', 'dropTime', 'isActive', 'isDeleted')
    list_filter = ('schoolID', 'sessionID', 'isActive', 'isDeleted')
    search_fields = ('=id', 'stopName', 'routeID__routeName')


@admin.register(TransportDriver)
class TransportDriverAdmin(admin.ModelAdmin):
    list_display = ('name', 'phoneNumber', 'licenseNumber', 'licenseExpiryDate', 'isActive', 'isDeleted')
    list_filter = ('schoolID', 'sessionID', 'isActive', 'isDeleted')
    search_fields = ('=id', 'name', 'phoneNumber', 'licenseNumber')


@admin.register(TransportVehicle)
class TransportVehicleAdmin(admin.ModelAdmin):
    list_display = ('vehicleNumber', 'vehicleType', 'capacity', 'driverID', 'routeID', 'isActive', 'isDeleted')
    list_filter = ('schoolID', 'sessionID', 'vehicleType', 'isActive', 'isDeleted')
    search_fields = ('=id', 'vehicleNumber', 'driverID__name', 'routeID__routeName')


@admin.register(TransportAssignment)
class TransportAssignmentAdmin(admin.ModelAdmin):
    list_display = ('assigneeType', 'studentID', 'teacherID', 'routeID', 'monthlyFee', 'feeMode', 'isActive', 'isDeleted')
    list_filter = ('schoolID', 'sessionID', 'assigneeType', 'feeMode', 'isActive', 'isDeleted')
    search_fields = ('=id', 'studentID__name', 'teacherID__name', 'routeID__routeName')


@admin.register(TransportFeeMapping)
class TransportFeeMappingAdmin(admin.ModelAdmin):
    list_display = ('routeID', 'stopID', 'assigneeType', 'feeMode', 'monthlyFee', 'effectiveFrom', 'effectiveTo', 'isActive', 'isDeleted')
    list_filter = ('schoolID', 'sessionID', 'assigneeType', 'feeMode', 'isActive', 'isDeleted')
    search_fields = ('=id', 'routeID__routeName', 'stopID__stopName')


@admin.register(TransportFeeRecord)
class TransportFeeRecordAdmin(admin.ModelAdmin):
    list_display = ('assignmentID', 'feeMonth', 'feeYear', 'netAmount', 'paidAmount', 'balanceAmount', 'status', 'isDeleted')
    list_filter = ('schoolID', 'sessionID', 'feeYear', 'feeMonth', 'status', 'isDeleted')
    search_fields = ('=id', 'assignmentID__studentID__name', 'assignmentID__teacherID__name', 'referenceNo')
