import csv
from datetime import date
from decimal import Decimal, InvalidOperation
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q, Sum
from django.http import HttpResponse, JsonResponse
from django.utils.html import escape
from django_datatables_view.base_datatable_view import BaseDatatableView

from utils.custom_response import ErrorResponse, SuccessResponse
from utils.logger import logger
from managementApp.access_control import has_management_permission
from managementApp.models import Student, TeacherDetail
from hostelApp.models import HostelRoom
from transportApp.models import TransportVehicle
from inventoryApp.models import (
    InventoryCategory,
    InventorySupplier,
    InventoryItem,
    InventoryStockLedger,
    InventoryAssetItem,
    InventoryAllocation,
)

def _current_session(request):
    return request.session.get('current_session', {}) or {}

def _school_id(request):
    return _current_session(request).get('SchoolID')

def _session_id(request):
    return _current_session(request).get('Id')

def _clean(value):
    if value is None:
        return None
    value = str(value).strip()
    if value.lower() in {'', 'undefined', 'null', 'none'}:
        return None
    return value

def _decimal(value):
    try:
        return Decimal(str(value or '0')).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal('0.00')

def _int(value, default=0):
    try:
        return int(str(value or '').strip())
    except (ValueError, TypeError):
        return default

def _user_label(request):
    return request.user.get_full_name() or request.user.username or str(request.user.id)

def _audit_fields(request, obj):
    obj.schoolID_id = _school_id(request)
    obj.sessionID_id = _session_id(request)
    obj.updatedByUserID = request.user
    obj.lastEditedBy = _user_label(request)

def _validation_message(exc, fallback):
    if isinstance(exc, ValidationError):
        if hasattr(exc, 'message_dict'):
            parts = []
            for field, messages in exc.message_dict.items():
                parts.append(f'{field}: {", ".join(str(message) for message in messages)}')
            return '; '.join(parts) or fallback
        if hasattr(exc, 'messages'):
            return '; '.join(str(message) for message in exc.messages) or fallback
    return str(exc) or fallback

def _scoped(model):
    return model.objects.filter(isDeleted=False)

def _scoped_for_request(request, model):
    return _scoped(model).filter(schoolID_id=_school_id(request), sessionID_id=_session_id(request))

def _scoped_object_or_new(request, model, object_id):
    object_id = _clean(object_id)
    if object_id:
        return _scoped_for_request(request, model).filter(pk=object_id).first()
    return model()

def _ensure_scoped_fk(request, model, object_id, label, optional=False):
    object_id = _clean(object_id)
    if not object_id:
        if optional:
            return None
        raise ValidationError(f'{label} is required.')
    if not _scoped_for_request(request, model).filter(pk=object_id).exists():
        raise ValidationError(f'{label} is invalid for current school/session.')
    return object_id

def _status_pill(active):
    label = 'Active' if active else 'Inactive'
    color = 'green' if active else 'grey'
    return f'<span class="ui {color} tiny label">{label}</span>'

def _choice_pill(label, color='blue'):
    return f'<span class="ui {color} tiny label">{escape(label or "N/A")}</span>'

def _inventory_button_allowed(request, action):
    return has_management_permission(request.user, 'inventory', action)

def _dt_actions(edit_fn, delete_fn, obj_id, request=None, extra_buttons=None):
    buttons = []
    if edit_fn and (not request or _inventory_button_allowed(request, 'edit')):
        buttons.append(
            f'<button data-inverted="" data-tooltip="Edit Detail" data-position="left center" '
            f'data-variation="mini" style="font-size:10px; margin-right: 4px;" onclick="{edit_fn}({obj_id})" '
            f'class="ui circular icon button green"><i class="pen icon"></i></button>'
        )
    if extra_buttons:
        buttons.extend(extra_buttons)
    if not request or _inventory_button_allowed(request, 'delete'):
        buttons.append(
            f'<button data-inverted="" data-tooltip="Delete" data-position="left center" '
            f'data-variation="mini" style="font-size:10px;" onclick="{delete_fn}({obj_id})" '
            f'class="ui circular icon button red"><i class="trash icon"></i></button>'
        )
    return ''.join(buttons)

def _create_allocation_stock_movement(request, allocation, transaction_type, quantity, notes, item_id=None):
    item_id = item_id or allocation.itemID_id
    if not item_id:
        return None
    ledger = InventoryStockLedger(
        itemID_id=item_id,
        transactionType=transaction_type,
        quantity=quantity,
        unitCost=Decimal('0.00'),
        referenceNo=f'ALLOC-{allocation.id}',
        notes=notes,
    )
    _audit_fields(request, ledger)
    ledger.full_clean()
    ledger.save()
    return ledger

# ----------------- DATATABLES VIEWS -----------------

class CategoryListJson(BaseDatatableView):
    order_columns = ['name', 'description', 'id']

    def get_initial_queryset(self):
        return _scoped_for_request(self.request, InventoryCategory)

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]')
        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(description__icontains=search))
        return qs

    def prepare_results(self, qs):
        return [
            [
                escape(i.name),
                escape(i.description or ''),
                _dt_actions('editCategory', 'confirmDeleteCategory', i.id, request=self.request)
            ] for i in qs
        ]

class SupplierListJson(BaseDatatableView):
    order_columns = ['companyName', 'contactName', 'phone', 'email', 'id']

    def get_initial_queryset(self):
        return _scoped_for_request(self.request, InventorySupplier)

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]')
        if search:
            qs = qs.filter(Q(companyName__icontains=search) | Q(contactName__icontains=search) | Q(phone__icontains=search) | Q(email__icontains=search))
        return qs

    def prepare_results(self, qs):
        return [
            [
                escape(i.companyName),
                escape(i.contactName or 'N/A'),
                escape(i.phone or 'N/A'),
                escape(i.email or 'N/A'),
                _dt_actions('editSupplier', 'confirmDeleteSupplier', i.id, request=self.request)
            ] for i in qs
        ]

class ItemListJson(BaseDatatableView):
    order_columns = ['name', 'categoryID__name', 'itemType', 'sku', 'minStockAlertLevel', 'currentStockQty', 'id']

    def get_initial_queryset(self):
        return _scoped_for_request(self.request, InventoryItem).select_related('categoryID')

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]')
        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(categoryID__name__icontains=search) | Q(sku__icontains=search))
        category_filter = self.request.GET.get('categoryID')
        if category_filter:
            qs = qs.filter(categoryID_id=category_filter)
        type_filter = self.request.GET.get('itemType')
        if type_filter:
            qs = qs.filter(itemType=type_filter)
        return qs

    def prepare_results(self, qs):
        res = []
        for i in qs:
            type_label = "Fixed Asset" if i.itemType == 'asset' else "Consumable"
            type_color = "teal" if i.itemType == 'asset' else "blue"
            stock_qty = i.currentStockQty
            if stock_qty <= i.minStockAlertLevel:
                stock_display = f'<span class="ui red small label">{stock_qty} (Low)</span>'
            else:
                stock_display = f'<span class="ui green small label">{stock_qty}</span>'
                
            res.append([
                escape(i.name),
                escape(i.categoryID.name),
                _choice_pill(type_label, type_color),
                escape(i.sku or 'N/A'),
                escape(i.minStockAlertLevel),
                stock_display,
                _dt_actions('editItem', 'confirmDeleteItem', i.id, request=self.request)
            ])
        return res

class StockLedgerListJson(BaseDatatableView):
    order_columns = ['datetime', 'itemID__name', 'transactionType', 'quantity', 'unitCost', 'supplierID__companyName', 'referenceNo', 'id']

    def get_initial_queryset(self):
        return _scoped_for_request(self.request, InventoryStockLedger).select_related('itemID', 'supplierID')

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]')
        if search:
            qs = qs.filter(Q(itemID__name__icontains=search) | Q(referenceNo__icontains=search) | Q(supplierID__companyName__icontains=search))
        item_filter = self.request.GET.get('itemID')
        if item_filter:
            qs = qs.filter(itemID_id=item_filter)
        return qs

    def prepare_results(self, qs):
        tx_labels = {
            'stock_in': ('Stock In', 'green'),
            'stock_out': ('Stock Out', 'red'),
            'adjustment': ('Adjustment', 'yellow'),
        }
        res = []
        for i in qs:
            label, color = tx_labels.get(i.transactionType, (i.transactionType, 'grey'))
            res.append([
                i.datetime.strftime('%Y-%m-%d %H:%M'),
                escape(i.itemID.name),
                _choice_pill(label, color),
                escape(f"{i.quantity} {i.itemID.standardUnit}"),
                escape(f"{i.unitCost}"),
                escape(i.supplierID.companyName if i.supplierID else 'N/A'),
                escape(i.referenceNo or 'N/A'),
                _dt_actions('editStock', 'confirmDeleteStock', i.id, request=self.request)
            ])
        return res

class AllocationListJson(BaseDatatableView):
    order_columns = ['allocationDate', 'itemID__name', 'allocatedToTeacher__name', 'quantity', 'status', 'id']

    def get_initial_queryset(self):
        return _scoped_for_request(self.request, InventoryAllocation).select_related(
            'itemID', 'assetItemID', 'assetItemID__itemID', 'allocatedToTeacher', 'allocatedToStudent', 'allocatedToRoom', 'allocatedToVehicle'
        )

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]')
        if search:
            qs = qs.filter(
                Q(itemID__name__icontains=search) | 
                Q(assetItemID__assetTag__icontains=search) |
                Q(allocatedToLocation__icontains=search) |
                Q(allocatedToTeacher__name__icontains=search) |
                Q(allocatedToStudent__name__icontains=search)
            )
        status_filter = self.request.GET.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)
        return qs

    def prepare_results(self, qs):
        status_colors = {
            'allocated': 'orange',
            'returned': 'green',
            'lost': 'red',
            'damaged': 'purple',
        }
        res = []
        for i in qs:
            # Determine item label
            if i.allocationType == 'asset' and i.assetItemID:
                item_label = f"{i.assetItemID.assetTag} - {i.assetItemID.itemID.name}"
            else:
                item_label = i.itemID.name if i.itemID else 'N/A'

            # Determine destination label
            dest = 'N/A'
            if i.allocatedToTeacher:
                dest = f"{i.allocatedToTeacher.name} (Staff)"
            elif i.allocatedToStudent:
                dest = f"{i.allocatedToStudent.name} (Student)"
            elif i.allocatedToRoom:
                dest = f"{i.allocatedToRoom} (Hostel)"
            elif i.allocatedToVehicle:
                dest = f"{i.allocatedToVehicle.vehicleNumber} (Vehicle)"
            elif i.allocatedToLocation:
                dest = f"{i.allocatedToLocation} (Location)"

            # Status pill
            status_color = status_colors.get(i.status, 'grey')
            status_pill = _choice_pill(i.get_status_display(), status_color)

            # Extra return button if currently allocated
            extra_btns = []
            if i.status == 'allocated':
                extra_btns.append(
                    f'<button data-inverted="" data-tooltip="Return Allocation" data-position="left center" '
                    f'data-variation="mini" style="font-size:10px; margin-right: 4px;" onclick="returnAllocation({i.id})" '
                    f'class="ui circular icon button blue"><i class="undo icon"></i></button>'
                )

            res.append([
                i.allocationDate.strftime('%Y-%m-%d'),
                escape(item_label),
                escape(dest),
                escape(i.quantity),
                status_pill,
                _dt_actions(None, 'confirmDeleteAllocation', i.id, request=self.request, extra_buttons=extra_btns)
            ])
        return res

class AssetListJson(BaseDatatableView):
    order_columns = ['assetTag', 'itemID__name', 'serialNumber', 'status', 'id']

    def get_initial_queryset(self):
        qs = _scoped_for_request(self.request, InventoryAssetItem).select_related('itemID')
        item_filter = self.request.GET.get('itemID')
        if item_filter:
            qs = qs.filter(itemID_id=item_filter)
        return qs

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]')
        if search:
            qs = qs.filter(Q(assetTag__icontains=search) | Q(itemID__name__icontains=search) | Q(serialNumber__icontains=search))
        return qs

    def prepare_results(self, qs):
        status_colors = {
            'available': 'green',
            'allocated': 'orange',
            'maintenance': 'yellow',
            'retired': 'red',
        }
        res = []
        for i in qs:
            status_color = status_colors.get(i.status, 'grey')
            res.append([
                escape(i.assetTag),
                escape(i.itemID.name),
                escape(i.serialNumber or 'N/A'),
                _choice_pill(i.get_status_display(), status_color),
                _dt_actions('editAsset', 'confirmDeleteAsset', i.id, request=self.request)
            ])
        return res

# ----------------- CRUD API VIEWS -----------------

@login_required
# ----------------- CRUD API VIEWS -----------------

@login_required
def categories_api(request):
    if request.method == 'GET':
        try:
            categories = _scoped_for_request(request, InventoryCategory).order_by('name')
            data = [{'id': c.id, 'name': c.name} for c in categories]
            return SuccessResponse("Categories retrieved.", data=data).to_json_response()
        except Exception as e:
            logger.error(f"Error retrieving categories: {e}")
            return ErrorResponse("Error retrieving categories.").to_json_response()

    elif request.method == 'POST':
        if not has_management_permission(request.user, 'inventory', 'edit' if request.POST.get('id') else 'add'):
            logger.error(f"Permission denied for categories_api POST by user={request.user.id}")
            return ErrorResponse("Permission denied.").to_json_response()
        obj_id = request.POST.get('id')
        obj = _scoped_object_or_new(request, InventoryCategory, obj_id)
        obj.name = request.POST.get('name')
        obj.description = request.POST.get('description')
        _audit_fields(request, obj)
        try:
            obj.full_clean()
            obj.save()
            action_name = "updated" if obj_id else "created"
            logger.info(f"Inventory category {action_name} successfully: ID={obj.id}, Name={obj.name} by user={request.user.id}")
            return SuccessResponse("Category saved successfully.", data={'id': obj.id, 'name': obj.name}).to_json_response()
        except Exception as e:
            logger.error(f"Error in categories_api POST: {e}")
            return ErrorResponse(_validation_message(e, "Error saving category.")).to_json_response()

@login_required
def category_detail_api(request):
    try:
        obj_id = request.GET.get('id')
        obj = _scoped_for_request(request, InventoryCategory).filter(pk=obj_id).first()
        if not obj:
            logger.error(f"Category not found: ID={obj_id}")
            return ErrorResponse("Category not found.").to_json_response()
        return SuccessResponse("Category retrieved.", data={
            'id': obj.id,
            'name': obj.name,
            'description': obj.description or '',
        }).to_json_response()
    except Exception as e:
        logger.error(f"Error in category_detail_api: {e}")
        return ErrorResponse("Error retrieving category details.").to_json_response()

@login_required
def delete_category_api(request):
    if not has_management_permission(request.user, 'inventory', 'delete'):
        logger.error(f"Permission denied for delete_category_api by user={request.user.id}")
        return ErrorResponse("Permission denied.").to_json_response()
    obj_id = request.POST.get('id')
    try:
        obj = _scoped_for_request(request, InventoryCategory).filter(pk=obj_id).first()
        if not obj:
            logger.error(f"Category not found for deletion: ID={obj_id}")
            return ErrorResponse("Category not found.").to_json_response()
        
        # Check if category contains active items
        if _scoped_for_request(request, InventoryItem).filter(categoryID=obj).exists():
            logger.error(f"Cannot delete category containing registered items: ID={obj_id}")
            return ErrorResponse("Cannot delete category containing registered items.").to_json_response()
            
        obj.isDeleted = True
        _audit_fields(request, obj)
        obj.save()
        logger.info(f"Category deleted successfully: ID={obj_id}, Name={obj.name} by user={request.user.id}")
        return SuccessResponse("Category deleted successfully.").to_json_response()
    except Exception as e:
        logger.error(f"Error in delete_category_api: {e}")
        return ErrorResponse("Error deleting category.").to_json_response()

@login_required
def items_api(request):
    if request.method == 'GET':
        try:
            items = _scoped_for_request(request, InventoryItem).select_related('categoryID').order_by('name')
            data = [{
                'id': i.id, 
                'name': i.name, 
                'itemType': i.itemType,
                'standardUnit': i.standardUnit,
                'currentStockQty': i.currentStockQty,
            } for i in items]
            return SuccessResponse("Items retrieved.", data=data).to_json_response()
        except Exception as e:
            logger.error(f"Error retrieving items: {e}")
            return ErrorResponse("Error retrieving items.").to_json_response()

    elif request.method == 'POST':
        if not has_management_permission(request.user, 'inventory', 'edit' if request.POST.get('id') else 'add'):
            logger.error(f"Permission denied for items_api POST by user={request.user.id}")
            return ErrorResponse("Permission denied.").to_json_response()
        obj_id = request.POST.get('id')
        obj = _scoped_object_or_new(request, InventoryItem, obj_id)
        
        try:
            category_id = _ensure_scoped_fk(request, InventoryCategory, request.POST.get('categoryID'), "Category")
            obj.categoryID_id = category_id
            obj.name = request.POST.get('name')
            obj.itemType = request.POST.get('itemType', 'consumable')
            obj.sku = request.POST.get('sku')
            obj.description = request.POST.get('description')
            obj.standardUnit = request.POST.get('standardUnit', 'pcs')
            obj.minStockAlertLevel = _int(request.POST.get('minStockAlertLevel', 5))
            _audit_fields(request, obj)
            obj.full_clean()
            obj.save()
            action_name = "updated" if obj_id else "created"
            logger.info(f"Inventory item {action_name} successfully: ID={obj.id}, Name={obj.name} by user={request.user.id}")
            return SuccessResponse("Item saved successfully.", data={'id': obj.id, 'name': obj.name}).to_json_response()
        except Exception as e:
            logger.error(f"Error in items_api POST: {e}")
            return ErrorResponse(_validation_message(e, "Error saving item.")).to_json_response()

@login_required
def item_detail_api(request):
    try:
        obj_id = request.GET.get('id')
        obj = _scoped_for_request(request, InventoryItem).filter(pk=obj_id).first()
        if not obj:
            logger.error(f"Item not found: ID={obj_id}")
            return ErrorResponse("Item not found.").to_json_response()
        return SuccessResponse("Item retrieved.", data={
            'id': obj.id,
            'categoryID': obj.categoryID_id,
            'name': obj.name,
            'itemType': obj.itemType,
            'sku': obj.sku or '',
            'description': obj.description or '',
            'standardUnit': obj.standardUnit,
            'minStockAlertLevel': obj.minStockAlertLevel,
            'currentStockQty': obj.currentStockQty,
        }).to_json_response()
    except Exception as e:
        logger.error(f"Error in item_detail_api: {e}")
        return ErrorResponse("Error retrieving item details.").to_json_response()

@login_required
def delete_item_api(request):
    if not has_management_permission(request.user, 'inventory', 'delete'):
        logger.error(f"Permission denied for delete_item_api by user={request.user.id}")
        return ErrorResponse("Permission denied.").to_json_response()
    obj_id = request.POST.get('id')
    try:
        obj = _scoped_for_request(request, InventoryItem).filter(pk=obj_id).first()
        if not obj:
            logger.error(f"Item not found for deletion: ID={obj_id}")
            return ErrorResponse("Item not found.").to_json_response()
            
        # Check if allocations or stock exist
        if _scoped_for_request(request, InventoryAllocation).filter(itemID=obj).exists():
            logger.error(f"Cannot delete item that has allocations logged: ID={obj_id}")
            return ErrorResponse("Cannot delete item that has allocations logged.").to_json_response()
        if _scoped_for_request(request, InventoryStockLedger).filter(itemID=obj).exists():
            logger.error(f"Cannot delete item with history in stock ledger: ID={obj_id}")
            return ErrorResponse("Cannot delete item with history in stock ledger.").to_json_response()
            
        obj.isDeleted = True
        _audit_fields(request, obj)
        obj.save()
        logger.info(f"Item deleted successfully: ID={obj_id}, Name={obj.name} by user={request.user.id}")
        return SuccessResponse("Item deleted successfully.").to_json_response()
    except Exception as e:
        logger.error(f"Error in delete_item_api: {e}")
        return ErrorResponse("Error deleting item.").to_json_response()

@login_required
def suppliers_api(request):
    if request.method == 'GET':
        try:
            suppliers = _scoped_for_request(request, InventorySupplier).order_by('companyName')
            data = [{'id': s.id, 'companyName': s.companyName} for s in suppliers]
            return SuccessResponse("Suppliers retrieved.", data=data).to_json_response()
        except Exception as e:
            logger.error(f"Error retrieving suppliers: {e}")
            return ErrorResponse("Error retrieving suppliers.").to_json_response()

    elif request.method == 'POST':
        if not has_management_permission(request.user, 'inventory', 'edit' if request.POST.get('id') else 'add'):
            logger.error(f"Permission denied for suppliers_api POST by user={request.user.id}")
            return ErrorResponse("Permission denied.").to_json_response()
        obj_id = request.POST.get('id')
        obj = _scoped_object_or_new(request, InventorySupplier, obj_id)
        obj.companyName = request.POST.get('companyName')
        obj.contactName = request.POST.get('contactName')
        obj.phone = request.POST.get('phone')
        obj.email = request.POST.get('email')
        obj.address = request.POST.get('address')
        obj.taxId = request.POST.get('taxId')
        _audit_fields(request, obj)
        try:
            obj.full_clean()
            obj.save()
            action_name = "updated" if obj_id else "created"
            logger.info(f"Supplier {action_name} successfully: ID={obj.id}, CompanyName={obj.companyName} by user={request.user.id}")
            return SuccessResponse("Supplier saved successfully.", data={'id': obj.id, 'companyName': obj.companyName}).to_json_response()
        except Exception as e:
            logger.error(f"Error in suppliers_api POST: {e}")
            return ErrorResponse(_validation_message(e, "Error saving supplier.")).to_json_response()

@login_required
def supplier_detail_api(request):
    try:
        obj_id = request.GET.get('id')
        obj = _scoped_for_request(request, InventorySupplier).filter(pk=obj_id).first()
        if not obj:
            logger.error(f"Supplier not found: ID={obj_id}")
            return ErrorResponse("Supplier not found.").to_json_response()
        return SuccessResponse("Supplier retrieved.", data={
            'id': obj.id,
            'companyName': obj.companyName,
            'contactName': obj.contactName or '',
            'phone': obj.phone or '',
            'email': obj.email or '',
            'address': obj.address or '',
            'taxId': obj.taxId or '',
        }).to_json_response()
    except Exception as e:
        logger.error(f"Error in supplier_detail_api: {e}")
        return ErrorResponse("Error retrieving supplier details.").to_json_response()

@login_required
def delete_supplier_api(request):
    if not has_management_permission(request.user, 'inventory', 'delete'):
        logger.error(f"Permission denied for delete_supplier_api by user={request.user.id}")
        return ErrorResponse("Permission denied.").to_json_response()
    obj_id = request.POST.get('id')
    try:
        obj = _scoped_for_request(request, InventorySupplier).filter(pk=obj_id).first()
        if not obj:
            logger.error(f"Supplier not found for deletion: ID={obj_id}")
            return ErrorResponse("Supplier not found.").to_json_response()
            
        obj.isDeleted = True
        _audit_fields(request, obj)
        obj.save()
        logger.info(f"Supplier deleted successfully: ID={obj_id}, CompanyName={obj.companyName} by user={request.user.id}")
        return SuccessResponse("Supplier deleted successfully.").to_json_response()
    except Exception as e:
        logger.error(f"Error in delete_supplier_api: {e}")
        return ErrorResponse("Error deleting supplier.").to_json_response()

@login_required
def stock_ledger_api(request):
    if request.method == 'POST':
        if not has_management_permission(request.user, 'inventory', 'add'):
            logger.error(f"Permission denied for stock_ledger_api POST by user={request.user.id}")
            return ErrorResponse("Permission denied.").to_json_response()
        
        try:
            with transaction.atomic():
                obj_id = request.POST.get('id')
                obj = _scoped_object_or_new(request, InventoryStockLedger, obj_id)
                
                item_id = _ensure_scoped_fk(request, InventoryItem, request.POST.get('itemID'), "Item")
                obj.itemID_id = item_id
                
                supplier_id = _ensure_scoped_fk(request, InventorySupplier, request.POST.get('supplierID'), "Supplier", optional=True)
                obj.supplierID_id = supplier_id
                
                obj.transactionType = request.POST.get('transactionType', 'stock_in')
                
                qty = _int(request.POST.get('quantity'), 0)
                if qty <= 0:
                    raise ValidationError("Quantity must be greater than zero.")
                    
                # Store quantity as negative for stock_out/disposals
                if obj.transactionType == 'stock_out':
                    obj.quantity = -qty
                else:
                    obj.quantity = qty
                    
                obj.unitCost = _decimal(request.POST.get('unitCost'))
                obj.referenceNo = request.POST.get('referenceNo')
                obj.notes = request.POST.get('notes')
                
                _audit_fields(request, obj)
                obj.full_clean()
                obj.save()
                
                action_name = "updated" if obj_id else "registered"
                logger.info(f"Stock ledger transaction {action_name} successfully: ID={obj.id}, Item={obj.itemID.name}, Quantity={obj.quantity} by user={request.user.id}")
                return SuccessResponse("Stock ledger transaction registered.").to_json_response()
        except Exception as e:
            logger.error(f"Error in stock_ledger_api POST: {e}")
            return ErrorResponse(_validation_message(e, "Error registering stock transaction.")).to_json_response()

@login_required
def allocations_api(request):
    if request.method == 'POST':
        if not has_management_permission(request.user, 'inventory', 'edit' if request.POST.get('id') else 'add'):
            logger.error(f"Permission denied for allocations_api POST by user={request.user.id}")
            return ErrorResponse("Permission denied.").to_json_response()
            
        try:
            with transaction.atomic():
                obj_id = _clean(request.POST.get('id'))
                old_allocation = None
                old_stock_state = None
                if obj_id:
                    old_allocation = _scoped_for_request(request, InventoryAllocation).select_related('itemID').filter(pk=obj_id).first()
                    if not old_allocation:
                        raise ValidationError("Allocation not found.")
                    old_stock_state = {
                        'allocationType': old_allocation.allocationType,
                        'status': old_allocation.status,
                        'itemID_id': old_allocation.itemID_id,
                        'quantity': old_allocation.quantity,
                    }
                    obj = old_allocation
                else:
                    obj = InventoryAllocation()
                
                obj.allocationType = request.POST.get('allocationType', 'asset')
                
                if obj.allocationType == 'asset':
                    asset_id = _ensure_scoped_fk(request, InventoryAssetItem, request.POST.get('assetItemID'), "Asset Item")
                    obj.assetItemID_id = asset_id
                    asset_item = InventoryAssetItem.objects.get(pk=asset_id)
                    obj.itemID = asset_item.itemID
                    obj.quantity = 1
                    
                    # Update status of asset
                    if not obj_id: # Creating new allocation
                        if asset_item.status != 'available':
                            raise ValidationError("Selected asset is not currently available.")
                        asset_item.status = 'allocated'
                        asset_item.save()
                else:
                    item_id = _ensure_scoped_fk(request, InventoryItem, request.POST.get('itemID'), "Item")
                    item = _scoped_for_request(request, InventoryItem).select_for_update().filter(pk=item_id).first()
                    if not item:
                        raise ValidationError("Item is invalid for current school/session.")
                    if item.itemType != 'consumable':
                        raise ValidationError("Only consumable items can be allocated by quantity.")

                    obj.quantity = _int(request.POST.get('quantity'), 1)
                    if obj.quantity <= 0:
                        raise ValidationError("Quantity must be greater than 0.")

                    InventoryStockLedger.recalculate_item_stock(item)
                    item.refresh_from_db(fields=['currentStockQty'])
                    reusable_old_qty = 0
                    if (
                        old_stock_state
                        and old_stock_state['status'] == 'allocated'
                        and old_stock_state['allocationType'] == 'consumable'
                        and old_stock_state['itemID_id'] == item.id
                    ):
                        reusable_old_qty = old_stock_state['quantity']
                    if obj.quantity > item.currentStockQty + reusable_old_qty:
                        raise ValidationError(f"Only {item.currentStockQty} {item.standardUnit} available for allocation.")
                    obj.itemID_id = item_id
                        
                # Destination links
                target_type = request.POST.get('targetType') # staff, student, room, vehicle, location
                
                obj.allocatedToTeacher_id = None
                obj.allocatedToStudent_id = None
                obj.allocatedToRoom_id = None
                obj.allocatedToVehicle_id = None
                obj.allocatedToLocation = None
                
                if target_type == 'staff':
                    obj.allocatedToTeacher_id = _ensure_scoped_fk(request, TeacherDetail, request.POST.get('allocatedToTeacher'), "Staff member")
                elif target_type == 'student':
                    obj.allocatedToStudent_id = _ensure_scoped_fk(request, Student, request.POST.get('allocatedToStudent'), "Student")
                elif target_type == 'room':
                    obj.allocatedToRoom_id = _ensure_scoped_fk(request, HostelRoom, request.POST.get('allocatedToRoom'), "Hostel Room")
                elif target_type == 'vehicle':
                    obj.allocatedToVehicle_id = _ensure_scoped_fk(request, TransportVehicle, request.POST.get('allocatedToVehicle'), "Vehicle")
                elif target_type == 'location':
                    obj.allocatedToLocation = request.POST.get('allocatedToLocation')
                    if not obj.allocatedToLocation:
                        raise ValidationError("Custom Location text is required.")
                else:
                    raise ValidationError("Invalid destination target type.")
                    
                obj.allocationDate = date.today()
                
                expected_return = request.POST.get('expectedReturnDate')
                if expected_return:
                    obj.expectedReturnDate = expected_return
                    
                _audit_fields(request, obj)
                obj.full_clean()
                obj.save()

                if old_stock_state and old_stock_state['status'] == 'allocated' and old_stock_state['allocationType'] == 'consumable':
                    _create_allocation_stock_movement(
                        request,
                        obj,
                        'stock_in',
                        old_stock_state['quantity'],
                        f'Restored previous consumable allocation #{obj.id} before update.',
                        item_id=old_stock_state['itemID_id'],
                    )
                if obj.status == 'allocated' and obj.allocationType == 'consumable':
                    _create_allocation_stock_movement(
                        request,
                        obj,
                        'stock_out',
                        obj.quantity,
                        f'Issued consumable allocation #{obj.id}.',
                    )
                
                action_name = "updated" if obj_id else "processed"
                logger.info(f"Inventory allocation {action_name} successfully: ID={obj.id}, Item={obj.itemID.name if obj.itemID else 'N/A'}, DestinationType={target_type} by user={request.user.id}")
                return SuccessResponse("Allocation processed successfully.").to_json_response()
        except Exception as e:
            logger.error(f"Error in allocations_api POST: {e}")
            return ErrorResponse(_validation_message(e, "Error saving allocation.")).to_json_response()

@login_required
def return_allocation_api(request):
    if not has_management_permission(request.user, 'inventory', 'edit'):
        logger.error(f"Permission denied for return_allocation_api by user={request.user.id}")
        return ErrorResponse("Permission denied.").to_json_response()
    obj_id = request.POST.get('id')
    
    try:
        with transaction.atomic():
            obj = _scoped_for_request(request, InventoryAllocation).filter(pk=obj_id).first()
            if not obj:
                logger.error(f"Allocation not found for return: ID={obj_id}")
                return ErrorResponse("Allocation not found.").to_json_response()
                
            if obj.status == 'returned':
                logger.error(f"Allocation already returned: ID={obj_id}")
                return ErrorResponse("This allocation has already been returned.").to_json_response()

            was_allocated = obj.status == 'allocated'
            obj.status = 'returned'
            obj.actualReturnDate = date.today()
            
            # Reset asset status to available
            if obj.allocationType == 'asset' and obj.assetItemID:
                obj.assetItemID.status = 'available'
                obj.assetItemID.save()
                
            _audit_fields(request, obj)
            obj.save()
            if was_allocated and obj.allocationType == 'consumable':
                _create_allocation_stock_movement(
                    request,
                    obj,
                    'stock_in',
                    obj.quantity,
                    f'Returned consumable allocation #{obj.id}.',
                )
            logger.info(f"Allocation returned successfully: ID={obj_id} by user={request.user.id}")
            return SuccessResponse("Allocation returned successfully.").to_json_response()
    except Exception as e:
        logger.error(f"Error in return_allocation_api: {e}")
        return ErrorResponse(_validation_message(e, "Error returning allocation.")).to_json_response()

@login_required
def delete_allocation_api(request):
    if not has_management_permission(request.user, 'inventory', 'delete'):
        logger.error(f"Permission denied for delete_allocation_api by user={request.user.id}")
        return ErrorResponse("Permission denied.").to_json_response()
    obj_id = request.POST.get('id')
    
    try:
        with transaction.atomic():
            obj = _scoped_for_request(request, InventoryAllocation).filter(pk=obj_id).first()
            if not obj:
                logger.error(f"Allocation not found for deletion: ID={obj_id}")
                return ErrorResponse("Allocation not found.").to_json_response()
                
            # If deleted while allocated, reset asset status to available
            if obj.status == 'allocated' and obj.allocationType == 'asset' and obj.assetItemID:
                obj.assetItemID.status = 'available'
                obj.assetItemID.save()
            elif obj.status == 'allocated' and obj.allocationType == 'consumable':
                _create_allocation_stock_movement(
                    request,
                    obj,
                    'stock_in',
                    obj.quantity,
                    f'Restored consumable allocation #{obj.id} during deletion.',
                )
                
            obj.isDeleted = True
            _audit_fields(request, obj)
            obj.save()
            logger.info(f"Allocation deleted successfully: ID={obj_id} by user={request.user.id}")
            return SuccessResponse("Allocation deleted successfully.").to_json_response()
    except Exception as e:
        logger.error(f"Error in delete_allocation_api: {e}")
        return ErrorResponse(_validation_message(e, "Error deleting allocation.")).to_json_response()

@login_required
def low_stock_api(request):
    try:
        # Retrieve items where stock qty is less than or equal to minimum stock alert level
        items = _scoped_for_request(request, InventoryItem).filter(currentStockQty__lte=models.F('minStockAlertLevel')).select_related('categoryID')
        data = [{
            'id': i.id,
            'name': i.name,
            'category': i.categoryID.name,
            'current': i.currentStockQty,
            'minLevel': i.minStockAlertLevel,
            'unit': i.standardUnit
        } for i in items]
        return SuccessResponse("Low stock items retrieved.", data=data).to_json_response()
    except Exception as e:
        logger.error(f"Error in low_stock_api: {e}")
        return ErrorResponse("Error retrieving low stock items.").to_json_response()

# ----------------- ASSET REGISTRY CRUD API -----------------

@login_required
def assets_api(request):
    if request.method == 'GET':
        try:
            assets = _scoped_for_request(request, InventoryAssetItem).select_related('itemID').order_by('assetTag')
            # Filter available assets if requested
            if request.GET.get('available_only') == 'true':
                assets = assets.filter(status='available')
            data = [{
                'id': a.id,
                'assetTag': a.assetTag,
                'serialNumber': a.serialNumber or 'N/A',
                'status': a.get_status_display(),
                'itemName': a.itemID.name
            } for a in assets]
            return SuccessResponse("Assets retrieved.", data=data).to_json_response()
        except Exception as e:
            logger.error(f"Error retrieving assets: {e}")
            return ErrorResponse("Error retrieving assets.").to_json_response()

    elif request.method == 'POST':
        if not has_management_permission(request.user, 'inventory', 'edit' if request.POST.get('id') else 'add'):
            logger.error(f"Permission denied for assets_api POST by user={request.user.id}")
            return ErrorResponse("Permission denied.").to_json_response()
        obj_id = request.POST.get('id')
        obj = _scoped_object_or_new(request, InventoryAssetItem, obj_id)
        
        try:
            item_id = _ensure_scoped_fk(request, InventoryItem, request.POST.get('itemID'), "Base Item")
            obj.itemID_id = item_id
            
            # Validate base item type is 'asset'
            item = InventoryItem.objects.get(pk=item_id)
            if item.itemType != 'asset':
                raise ValidationError("You can only register asset tags for items marked as Fixed Assets.")
                
            obj.assetTag = request.POST.get('assetTag')
            obj.serialNumber = request.POST.get('serialNumber')
            obj.status = request.POST.get('status', 'available')
            obj.notes = request.POST.get('notes')
            
            _audit_fields(request, obj)
            obj.full_clean()
            obj.save()
            
            action_name = "updated" if obj_id else "registered"
            logger.info(f"Asset tag {action_name} successfully: ID={obj.id}, AssetTag={obj.assetTag} by user={request.user.id}")
            return SuccessResponse("Asset tag saved successfully.").to_json_response()
        except Exception as e:
            logger.error(f"Error in assets_api POST: {e}")
            return ErrorResponse(_validation_message(e, "Error saving asset.")).to_json_response()

@login_required
def asset_detail_api(request):
    try:
        obj_id = request.GET.get('id')
        obj = _scoped_for_request(request, InventoryAssetItem).filter(pk=obj_id).first()
        if not obj:
            logger.error(f"Asset not found: ID={obj_id}")
            return ErrorResponse("Asset not found.").to_json_response()
        return SuccessResponse("Asset retrieved.", data={
            'id': obj.id,
            'itemID': obj.itemID_id,
            'assetTag': obj.assetTag,
            'serialNumber': obj.serialNumber or '',
            'status': obj.status,
            'notes': obj.notes or '',
        }).to_json_response()
    except Exception as e:
        logger.error(f"Error in asset_detail_api: {e}")
        return ErrorResponse("Error retrieving asset details.").to_json_response()

@login_required
def delete_asset_api(request):
    if not has_management_permission(request.user, 'inventory', 'delete'):
        logger.error(f"Permission denied for delete_asset_api by user={request.user.id}")
        return ErrorResponse("Permission denied.").to_json_response()
    obj_id = request.POST.get('id')
    try:
        obj = _scoped_for_request(request, InventoryAssetItem).filter(pk=obj_id).first()
        if not obj:
            logger.error(f"Asset not found for deletion: ID={obj_id}")
            return ErrorResponse("Asset not found.").to_json_response()
            
        # Check if allocated
        if obj.status == 'allocated':
            logger.error(f"Cannot delete an allocated asset tag: ID={obj_id}")
            return ErrorResponse("Cannot delete an asset tag that is currently allocated to someone.").to_json_response()
            
        obj.isDeleted = True
        _audit_fields(request, obj)
        obj.save()
        logger.info(f"Asset deleted successfully: ID={obj_id}, AssetTag={obj.assetTag} by user={request.user.id}")
        return SuccessResponse("Asset deleted successfully.").to_json_response()
    except Exception as e:
        logger.error(f"Error in delete_asset_api: {e}")
        return ErrorResponse("Error deleting asset.").to_json_response()

@login_required
def dashboard_summary(request):
    try:
        categories_count = _scoped_for_request(request, InventoryCategory).count()
        items_count = _scoped_for_request(request, InventoryItem).count()
        assets_count = _scoped_for_request(request, InventoryAssetItem).count()
        allocations_count = _scoped_for_request(request, InventoryAllocation).filter(status='allocated').count()
        low_stock_count = _scoped_for_request(request, InventoryItem).filter(currentStockQty__lte=models.F('minStockAlertLevel')).count()
        
        total_value = _scoped_for_request(request, InventoryStockLedger).aggregate(val=Sum(models.F('quantity') * models.F('unitCost')))['val'] or Decimal('0.00')

        item_types = list(
            _scoped_for_request(request, InventoryItem)
            .values('itemType')
            .annotate(count=models.Count('id'))
            .order_by('itemType')
        )
        asset_statuses = list(
            _scoped_for_request(request, InventoryAssetItem)
            .values('status')
            .annotate(count=models.Count('id'))
            .order_by('status')
        )
        allocation_statuses = list(
            _scoped_for_request(request, InventoryAllocation)
            .values('status')
            .annotate(count=models.Count('id'))
            .order_by('status')
        )
        stock_by_category = list(
            _scoped_for_request(request, InventoryItem)
            .values('categoryID__name')
            .annotate(total=models.Sum('currentStockQty'))
            .order_by('-total')[:8]
        )
        top_stock_items = list(
            _scoped_for_request(request, InventoryItem)
            .filter(itemType='consumable')
            .order_by('-currentStockQty', 'name')
            .values('name', 'currentStockQty', 'standardUnit')[:8]
        )
        
        return SuccessResponse("Dashboard summary loaded.", data={
            'categoriesCount': categories_count,
            'itemsCount': items_count,
            'assetsCount': assets_count,
            'allocationsCount': allocations_count,
            'lowStockCount': low_stock_count,
            'totalValue': f"{total_value:,.2f}",
            'charts': {
                'itemTypes': {
                    'labels': [dict(InventoryItem.ITEM_TYPE_CHOICES).get(row['itemType'], row['itemType'] or 'N/A') for row in item_types],
                    'values': [row['count'] for row in item_types],
                },
                'assetStatuses': {
                    'labels': [dict(InventoryAssetItem.STATUS_CHOICES).get(row['status'], row['status'] or 'N/A') for row in asset_statuses],
                    'values': [row['count'] for row in asset_statuses],
                },
                'allocationStatuses': {
                    'labels': [dict(InventoryAllocation.ALLOCATION_STATUS).get(row['status'], row['status'] or 'N/A') for row in allocation_statuses],
                    'values': [row['count'] for row in allocation_statuses],
                },
                'stockByCategory': {
                    'labels': [row['categoryID__name'] or 'Uncategorized' for row in stock_by_category],
                    'values': [row['total'] or 0 for row in stock_by_category],
                },
                'topStockItems': {
                    'labels': [row['name'] for row in top_stock_items],
                    'values': [row['currentStockQty'] for row in top_stock_items],
                    'units': [row['standardUnit'] for row in top_stock_items],
                },
            }
        }).to_json_response()
    except Exception as e:
        logger.error(f"Error in dashboard_summary: {e}")
        return ErrorResponse("Error loading dashboard summary.").to_json_response()
