from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from rest_framework import permissions, serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.properties.models import Property

from .services import (
    accessible_properties,
    compute_consultant_scope_report,
    compute_property_report,
    get_property_for_user_or_403,
    property_report_csv_rows,
    render_csv,
)


class IsAuthenticatedRole(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)


def _parse_date(raw):
    import datetime

    if not raw:
        return None
    try:
        return datetime.datetime.strptime(raw, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


class PropertyReportView(APIView):
    """Scoped analytics for a single property.

    GET /api/reports/properties/<id>/          → KPIs + chart series + warnings
    GET /api/reports/properties/<id>/?date_from=...&date_to=... → window filter
    """

    permission_classes = [IsAuthenticatedRole]

    def _report(self, request, property_id):
        try:
            pid = int(property_id)
        except (TypeError, ValueError):
            raise ValueError("شناسه ملک نامعتبر است.")
        prop = get_property_for_user_or_403(request.user, pid)
        filters = {
            "date_from": _parse_date(request.query_params.get("date_from")),
            "date_to": _parse_date(request.query_params.get("date_to")),
        }
        filters = {k: v for k, v in filters.items() if v is not None}
        return pid, compute_property_report(prop, filters=filters)

    def get(self, request, property_id):
        try:
            _, report = self._report(request, property_id)
        except ValueError as e:
            return Response({"detail": str(e)}, status=400)
        return Response(report)


class PropertyReportExportView(APIView):
    """CSV export with identical permission scope/filtering as the JSON view."""

    permission_classes = [IsAuthenticatedRole]

    def get(self, request, property_id):
        view = PropertyReportView()
        view.request = request
        view.format_kwarg = None
        try:
            pid, report = view._report(request, property_id)
        except ValueError as e:
            return Response({"detail": str(e)}, status=400)

        # Record the export in the activity log so it is auditable like any
        # other mutating action. The current user is picked up automatically
        # by ``log_activity`` via the thread-local request.
        from apps.common.activity import log_activity
        from apps.common.models import ActivityLog

        title = report.get("property", {}).get("title") or f"ملک {pid}"
        log_activity(
            user=request.user,
            action=ActivityLog.ActionType.EXPORT,
            target_type=ActivityLog.TargetType.PROPERTY,
            target_id=pid,
            description=f"گزارش کامل ملک «{title}» به‌صورت CSV دریافت شد",
            metadata={
                "format": "csv",
                "property_id": pid,
                "date_from": request.query_params.get("date_from"),
                "date_to": request.query_params.get("date_to"),
            },
        )

        rows = property_report_csv_rows(report)
        from .services import CSV_TRANSLATIONS as _TR

        key_to_label = {
            "propertyId": "شناسه ملک",
            "title": "عنوان",
            "internalCode": "کد داخلی",
        }
        for k, lbl in _TR.items():
            key_to_label[k] = lbl
        fieldnames = ["شناسه ملک", "عنوان", "کد داخلی"] + [
            label for label in _TR.values() if label not in {"شناسه ملک", "عنوان", "کد داخلی"}
        ]
        persian_rows = [
            {key_to_label.get(k, k): v for k, v in r.items()} for r in rows
        ]
        csv_text = render_csv(persian_rows, fieldnames=fieldnames)
        resp = HttpResponse(csv_text, content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = (
            f'attachment; filename="property-report-{pid}.csv"'
        )
        return resp


class PropertyReportPdfView(APIView):
    """PDF export of the full property report.

    Same scoped data as the JSON/CSV exports, rendered to a printed report
    (property info → KPIs → listings → follow-ups → charts → activity log).

    Access is deliberately one step stricter than the on-screen report:
    admins may export any property, while consultants may only export the
    properties they are assigned to or that are shared with them.
    Every export is written to the activity log, exactly like the CSV one.
    """

    permission_classes = [IsAuthenticatedRole]

    def get(self, request, property_id):
        try:
            pid = int(property_id)
        except (TypeError, ValueError):
            return Response({"detail": "شناسه ملک نامعتبر است."}, status=400)

        from apps.common.access import can_access_property
        from apps.properties.models import Property as PropertyModel

        prop = (
            PropertyModel.objects.select_related(
                "consultant",
                "property_type_ref",
                "property_usage",
                "district",
                "district__city",
                "district__city__province",
            )
            .prefetch_related("listings__deal_type", "tasks", "followups", "images")
            .filter(pk=pid)
            .first()
        )
        if prop is None:
            return Response({"detail": "ملک مورد نظر وجود ندارد."}, status=404)
        if not can_access_property(request.user, prop):
            return Response({"detail": "شما به گزارش این ملک دسترسی ندارید."}, status=403)

        filters = {
            "date_from": _parse_date(request.query_params.get("date_from")),
            "date_to": _parse_date(request.query_params.get("date_to")),
        }
        filters = {k: v for k, v in filters.items() if v is not None}
        report = compute_property_report(prop, filters=filters)

        # Record the export in the activity log, mirroring the CSV export.
        from apps.common.activity import log_activity
        from apps.common.models import ActivityLog

        log_activity(
            user=request.user,
            action=ActivityLog.ActionType.EXPORT,
            target_type=ActivityLog.TargetType.PROPERTY,
            target_id=pid,
            description=f"گزارش کامل ملک «{prop.title}» به‌صورت PDF دریافت شد",
            metadata={
                "format": "pdf",
                "property_id": pid,
                "date_from": request.query_params.get("date_from"),
                "date_to": request.query_params.get("date_to"),
            },
        )

        from .pdf import build_property_pdf

        pdf_bytes = build_property_pdf(prop, report, request.user)
        resp = HttpResponse(pdf_bytes, content_type="application/pdf")
        resp["Content-Disposition"] = f'attachment; filename="property-report-{pid}.pdf"'
        return resp


class ConsultantScopeReportView(APIView):
    """Aggregate KPIs within the caller's scope.

    Admin: portfolio-wide. Consultant: their own properties.
    """

    permission_classes = [IsAuthenticatedRole]

    def get(self, request):
        data = compute_consultant_scope_report(request.user)
        return Response(data)


class PropertyOptionsView(APIView):
    """Dropdown/filter metadata (properties the user can scope into)."""

    permission_classes = [IsAuthenticatedRole]

    def get(self, request):
        qs = accessible_properties(request.user).order_by("-created_at").values_list(
            "id", "title", "internal_code", "neighborhood", "status"
        )
        options = [
            {
                "id": pid,
                "title": title,
                "internalCode": code,
                "neighborhood": nb,
                "status": st,
            }
            for pid, title, code, nb, st in qs[:200]
        ]
        return Response({"properties": options, "count": len(options)})
