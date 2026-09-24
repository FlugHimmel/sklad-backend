from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    ContainerEventViewSet, ContainerViewSet, FreeStockView,
    InventoryViewSet, MonthlySummaryView, MovementReportView, MovementViewSet,
    OrderFulfillmentReportView, StockReportView, StockViewSet, WarehouseViewSet,
)
from .views_container_fill import ContainerFillView
from .views_container_lines import (
    ContainerLineAddView, ContainerLineSetQuantityView, ContainerTransferToView,
)
from .views_container_state import (
    ContainerBulkSetPackedView, ContainerSetPackedView,
)
from .views_empty_bulk import BulkCreateEmptyView, EmptyContainersView
from .views_operations import (
    ContainerOpHistoryView, OperationDetailView, OperationListCreateView,
    OperationMetaView, OperationRollbackView,
)
from .views_events import EventsView
from .views_reports_ops import (
    ProductionOpsReportView, ReadyToShipReportView, ScrapOpsReportView,
)
from .views_ship_scan import BulkShipDeleteView
from .views_shipment_notes import (
    BulkSimplePackingPdfView, ContainerPackingListPdfView,
    CreateNoteForTodayView, ShipmentNoteCreateView, ShipmentNoteDetailView,
    ShipmentNoteListView, ShipmentNotePdfView,
)

router = DefaultRouter()
router.register("warehouses", WarehouseViewSet, basename="warehouses")
router.register("stocks", StockViewSet, basename="stocks")
router.register("containers", ContainerViewSet, basename="containers")
router.register("container-events", ContainerEventViewSet, basename="container-events")
router.register("movements", MovementViewSet, basename="movements")
router.register("inventories", InventoryViewSet, basename="inventories")

urlpatterns = [
    # Тара
    path("containers/bulk-ship-delete/", BulkShipDeleteView.as_view(),
         name="containers-bulk-ship-delete"),
    path("containers/empty/", EmptyContainersView.as_view(),
         name="containers-empty"),
    path("containers/bulk-create-empty/", BulkCreateEmptyView.as_view(),
         name="containers-bulk-create-empty"),
    path("containers/bulk-packing-pdf/", BulkSimplePackingPdfView.as_view(),
         name="containers-bulk-packing-pdf"),
    path("containers/<int:pk>/fill/", ContainerFillView.as_view(),
         name="container-fill"),
    path("containers/<int:pk>/set-packed/", ContainerSetPackedView.as_view(),
         name="container-set-packed"),
    path("containers/bulk-set-packed/", ContainerBulkSetPackedView.as_view(),
         name="containers-bulk-set-packed"),
    path("containers/<int:pk>/lines/<int:line_id>/set/",
         ContainerLineSetQuantityView.as_view(), name="container-line-set"),
    path("containers/<int:pk>/lines/add/", ContainerLineAddView.as_view(),
         name="container-line-add"),
    path("containers/<int:pk>/transfer-to/", ContainerTransferToView.as_view(),
         name="container-transfer-to"),
    path("containers/<int:pk>/simple-packing-pdf/",
         ContainerPackingListPdfView.as_view(), name="container-simple-packing-pdf"),
    path("containers/<int:pk>/op-history/", ContainerOpHistoryView.as_view(),
         name="container-op-history"),

    # Накладные
    path("shipment-notes/create/", ShipmentNoteCreateView.as_view(),
         name="shipment-notes-create"),
    path("shipment-notes/create-for-today/",
         CreateNoteForTodayView.as_view(),
         name="shipment-notes-create-for-today"),
    path("shipment-notes/", ShipmentNoteListView.as_view(),
         name="shipment-notes-list"),
    path("shipment-notes/<int:pk>/", ShipmentNoteDetailView.as_view(),
         name="shipment-notes-detail"),
    path("shipment-notes/<int:pk>/pdf/", ShipmentNotePdfView.as_view(),
         name="shipment-notes-pdf"),

    # Отчёты
    path("products/<int:product_id>/free-stock/", FreeStockView.as_view(),
         name="product-free-stock"),
    path("reports/stock/", StockReportView.as_view(), name="report-stock"),
    path("reports/movements/", MovementReportView.as_view(), name="report-movements"),
    path("reports/monthly-summary/", MonthlySummaryView.as_view(),
         name="report-monthly-summary"),
    path("reports/order-fulfillment/", OrderFulfillmentReportView.as_view(),
         name="report-order-fulfillment"),
    path("reports/production-ops/", ProductionOpsReportView.as_view(),
         name="report-production-ops"),
    path("reports/scrap-ops/", ScrapOpsReportView.as_view(),
         name="report-scrap-ops"),
    path("reports/ready-to-ship/", ReadyToShipReportView.as_view(),
         name="report-ready-to-ship"),
    path("reports/events/", EventsView.as_view(), name="report-events"),

    # Операции
    path("operations/meta/", OperationMetaView.as_view(), name="operations-meta"),
    path("operations/", OperationListCreateView.as_view(),
         name="operations-list-create"),
    path("operations/<int:pk>/", OperationDetailView.as_view(),
         name="operations-detail"),
    path("operations/<int:pk>/rollback/", OperationRollbackView.as_view(),
         name="operations-rollback"),
] + router.urls
