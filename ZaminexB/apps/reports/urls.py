from django.urls import path

from .views import (
    ConsultantScopeReportView,
    PropertyOptionsView,
    PropertyReportExportView,
    PropertyReportView,
    property_report_print,
)

app_name = "reports"

urlpatterns = [
    path(
        "api/reports/properties/<int:property_id>/export/",
        PropertyReportExportView.as_view(),
        name="property-report-export",
    ),
    path(
        "api/reports/properties/<int:property_id>/",
        PropertyReportView.as_view(),
        name="property-report",
    ),
    path(
        "api/reports/scope/",
        ConsultantScopeReportView.as_view(),
        name="consultant-scope-report",
    ),
    path(
        "api/reports/property-options/",
        PropertyOptionsView.as_view(),
        name="property-report-options",
    ),
    # Print-ready HTML report: the SPA's «خروجی PDF» button opens this in a
    # new tab; the browser's native print dialog then turns it into paper or
    # «Save as PDF». It is an HTML page (not an API), hence outside api/.
    path(
        "reports/properties/<int:property_id>/print/",
        property_report_print,
        name="property-report-print",
    ),
]
