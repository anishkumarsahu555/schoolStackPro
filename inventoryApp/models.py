from django.db import models
from django.contrib.auth.models import User
from homeApp.model_mixins import SchoolScopedModel
from managementApp.models import Student, TeacherDetail
from hostelApp.models import HostelRoom
from transportApp.models import TransportVehicle

class InventoryAuditModel(SchoolScopedModel):
    lastEditedBy = models.CharField(max_length=500, blank=True, null=True)
    updatedByUserID = models.ForeignKey(User, blank=True, null=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        abstract = True

class InventoryCategory(InventoryAuditModel):
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = "Inventory Categories"
        constraints = [
            models.UniqueConstraint(
                fields=['schoolID', 'sessionID', 'name'],
                condition=models.Q(isDeleted=False),
                name='unique_inventory_category_name'
            )
        ]

class InventorySupplier(InventoryAuditModel):
    companyName = models.CharField(max_length=200)
    contactName = models.CharField(max_length=200, blank=True, null=True)
    phone = models.CharField(max_length=30, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    taxId = models.CharField(max_length=80, blank=True, null=True)

    def __str__(self):
        return self.companyName

    class Meta:
        verbose_name_plural = "Inventory Suppliers"
        constraints = [
            models.UniqueConstraint(
                fields=['schoolID', 'sessionID', 'companyName'],
                condition=models.Q(isDeleted=False),
                name='unique_inventory_supplier_company'
            )
        ]

class InventoryItem(InventoryAuditModel):
    ITEM_TYPE_CHOICES = (
        ('consumable', 'Consumable (Stocked Qty)'),
        ('asset', 'Fixed Asset (Serialized Items)'),
    )
    categoryID = models.ForeignKey(InventoryCategory, on_delete=models.PROTECT, related_name='items')
    name = models.CharField(max_length=200)
    itemType = models.CharField(max_length=20, choices=ITEM_TYPE_CHOICES, default='consumable')
    sku = models.CharField(max_length=100, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    standardUnit = models.CharField(max_length=50, default='pcs', help_text='e.g., pcs, boxes, meters')
    minStockAlertLevel = models.PositiveIntegerField(default=5)
    currentStockQty = models.IntegerField(default=0)

    def __str__(self):
        return f"{self.name} ({self.get_itemType_display()})"

    class Meta:
        verbose_name_plural = "Inventory Items"
        constraints = [
            models.UniqueConstraint(
                fields=['schoolID', 'sessionID', 'name', 'categoryID'],
                condition=models.Q(isDeleted=False),
                name='unique_inventory_item_name_category'
            )
        ]

class InventoryStockLedger(InventoryAuditModel):
    TX_TYPE_CHOICES = (
        ('stock_in', 'Stock In (Purchase/Donation)'),
        ('stock_out', 'Stock Out (Disposal/Usage)'),
        ('adjustment', 'Adjustment (Count correction)'),
    )
    itemID = models.ForeignKey(InventoryItem, on_delete=models.CASCADE, related_name='ledger')
    supplierID = models.ForeignKey(InventorySupplier, blank=True, null=True, on_delete=models.SET_NULL, related_name='ledger')
    transactionType = models.CharField(max_length=20, choices=TX_TYPE_CHOICES, default='stock_in')
    quantity = models.IntegerField()
    unitCost = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    referenceNo = models.CharField(max_length=150, blank=True, null=True, help_text="e.g. Invoice # or Voucher #")
    notes = models.TextField(blank=True, null=True)

    @staticmethod
    def recalculate_item_stock(item):
        if not item:
            return
        stock_sum = InventoryStockLedger.objects.filter(
            itemID=item, isDeleted=False
        ).aggregate(total=models.Sum('quantity'))['total'] or 0
        item.currentStockQty = stock_sum
        item.save(update_fields=['currentStockQty'])

    def save(self, *args, **kwargs):
        old_item = None
        if self.pk:
            old_item = InventoryStockLedger.objects.filter(pk=self.pk).select_related('itemID').first()
            old_item = old_item.itemID if old_item else None

        if self.transactionType == 'stock_out':
            self.quantity = -abs(int(self.quantity or 0))
        else:
            self.quantity = abs(int(self.quantity or 0))

        super().save(*args, **kwargs)
        self.recalculate_item_stock(self.itemID)
        if old_item and old_item.pk != self.itemID_id:
            self.recalculate_item_stock(old_item)

    class Meta:
        verbose_name_plural = "Inventory Stock Ledgers"

class InventoryAssetItem(InventoryAuditModel):
    STATUS_CHOICES = (
        ('available', 'Available'),
        ('allocated', 'Allocated'),
        ('maintenance', 'Under Maintenance'),
        ('retired', 'Retired'),
    )
    itemID = models.ForeignKey(InventoryItem, limit_choices_to={'itemType': 'asset'}, on_delete=models.PROTECT, related_name='assets')
    assetTag = models.CharField(max_length=100, help_text="Unique asset barcode or tag")
    serialNumber = models.CharField(max_length=150, blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='available')
    notes = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.assetTag} - {self.itemID.name}"

    class Meta:
        verbose_name_plural = "Inventory Asset Items"
        constraints = [
            models.UniqueConstraint(
                fields=['schoolID', 'assetTag'],
                condition=models.Q(isDeleted=False),
                name='unique_school_asset_tag'
            )
        ]

class InventoryAllocation(InventoryAuditModel):
    ALLOCATION_STATUS = (
        ('allocated', 'Allocated'),
        ('returned', 'Returned'),
        ('lost', 'Lost'),
        ('damaged', 'Damaged'),
    )
    allocationType = models.CharField(max_length=20, choices=(('consumable', 'Consumable'), ('asset', 'Fixed Asset')), default='asset')
    itemID = models.ForeignKey(InventoryItem, blank=True, null=True, on_delete=models.PROTECT, related_name='allocations')
    assetItemID = models.ForeignKey(InventoryAssetItem, blank=True, null=True, on_delete=models.PROTECT, related_name='allocations')
    
    # Entity allocations
    allocatedToTeacher = models.ForeignKey(TeacherDetail, blank=True, null=True, on_delete=models.PROTECT, related_name='inventoryAllocations')
    allocatedToStudent = models.ForeignKey(Student, blank=True, null=True, on_delete=models.PROTECT, related_name='inventoryAllocations')
    allocatedToRoom = models.ForeignKey(HostelRoom, blank=True, null=True, on_delete=models.PROTECT, related_name='inventoryAllocations')
    allocatedToVehicle = models.ForeignKey(TransportVehicle, blank=True, null=True, on_delete=models.PROTECT, related_name='inventoryAllocations')
    allocatedToLocation = models.CharField(max_length=200, blank=True, null=True, help_text="e.g. Science Lab B, Office")
    
    quantity = models.PositiveIntegerField(default=1)
    allocationDate = models.DateField()
    expectedReturnDate = models.DateField(blank=True, null=True)
    actualReturnDate = models.DateField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=ALLOCATION_STATUS, default='allocated')
    notes = models.TextField(blank=True, null=True)

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.allocationType == 'asset' and not self.assetItemID:
            raise ValidationError("Asset allocation requires selecting an Asset Item.")
        if self.allocationType == 'consumable' and not self.itemID:
            raise ValidationError("Consumable allocation requires selecting an Item.")
        targets = [self.allocatedToTeacher, self.allocatedToStudent, self.allocatedToRoom, self.allocatedToVehicle, self.allocatedToLocation]
        selected_targets = [t for t in targets if t]
        if len(selected_targets) == 0:
            raise ValidationError("You must specify a destination (Staff, Student, Room, Vehicle, or Custom Location).")
        if len(selected_targets) > 1:
            raise ValidationError("An allocation can only have one destination target.")

    class Meta:
        verbose_name_plural = "Inventory Allocations"
