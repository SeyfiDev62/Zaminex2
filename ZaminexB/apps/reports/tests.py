import csv
import datetime
import io
import re
from decimal import Decimal
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import ConsultantProfile, UserRole
from apps.activity.models import ActivityLog
from apps.followups.models import FollowUp
from apps.listings.models import Listing
from apps.properties.models import Property
from apps.tasks.models import Task

from . import pdf as pdf_mod
from .caching import cached_property_report
from .pdf import (
    _followups_section,
    _listings_section,
    _logs_section,
    _styles,
    _tasks_section,
    build_property_pdf,
    t,
)
from .services import compute_property_report, get_property_for_user_or_403

User = get_user_model()


class ReportsServiceTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="adm", password="x" * 10, role=UserRole.ADMIN
        )
        self.agent = User.objects.create_user(
            username="ag", password="x" * 10, role=UserRole.AGENT
        )
        ConsultantProfile.objects.create(
            user=self.agent, full_name="Agent A", branch="B"
        )
        self.agent2 = User.objects.create_user(
            username="ag2", password="x" * 10, role=UserRole.AGENT
        )
        ConsultantProfile.objects.create(
            user=self.agent2, full_name="Agent B", branch="B"
        )
        self.prop = Property.objects.create(
            title="Apt 1",
            internal_code="R1",
            consultant=self.agent,
            property_type=Property.PropertyType.APARTMENT,
            deal_type=Property.DealType.SALE,
            price=Decimal("1000000000"),
            area=100,
            rooms=2,
            address="addr",
            neighborhood="N1",
            latitude=Decimal("35.7"),
            longitude=Decimal("51.4"),
        )
        # comparable same neighborhood/type
        Property.objects.create(
            title="Apt 2",
            internal_code="R2",
            consultant=self.agent,
            property_type=Property.PropertyType.APARTMENT,
            deal_type=Property.DealType.SALE,
            price=Decimal("800000000"),
            area=100,
            rooms=2,
            address="addr",
            neighborhood="N1",
        )
        Property.objects.create(
            title="Apt 3",
            internal_code="R3",
            consultant=self.agent,
            property_type=Property.PropertyType.APARTMENT,
            deal_type=Property.DealType.SALE,
            price=Decimal("900000000"),
            area=100,
            rooms=2,
            address="addr",
            neighborhood="N1",
        )

        self.task_overdue = Task.objects.create(
            title="T1",
            assigned_to=self.agent,
            created_by=self.agent,
            property=self.prop,
            due_date=datetime.date.today() - datetime.timedelta(days=3),
            task_type=Task.TaskType.VIEWING,
            status=Task.Status.PENDING,
        )
        self.task_done = Task.objects.create(
            title="T2",
            assigned_to=self.agent,
            created_by=self.agent,
            property=self.prop,
            due_date=datetime.date.today() + datetime.timedelta(days=2),
            task_type=Task.TaskType.DOCUMENT,
            status=Task.Status.COMPLETED,
            completed_at=timezone.now(),
        )
        self.followup = FollowUp.objects.create(
            title="F1",
            consultant=self.agent,
            contact_name="c1",
            property=self.prop,
            probability=60,
            scheduled_at=timezone.now() - datetime.timedelta(days=2),
        )
        self.listing = Listing.objects.create(
            property=self.prop,
            title="L1",
            publish_channel=Listing.PublishChannel.WEBSITE,
            created_by=self.agent,
            assigned_to=self.agent,
            start_date=timezone.now() - datetime.timedelta(days=5),
        )

    def test_property_kpis_and_scoping(self):
        r = compute_property_report(self.prop)
        self.assertEqual(r["property"]["id"], self.prop.pk)
        self.assertEqual(r["kpis"]["tasksOverdueCount"], 1)
        self.assertEqual(r["kpis"]["followupsOverdueCount"], 1)
        self.assertEqual(r["kpis"]["imagesCount"], 0)
        self.assertEqual(r["kpis"]["pricePerSqm"], 10_000_000.0)
        self.assertTrue(r["kpis"]["geoPrecisionFlag"])
        self.assertEqual(r["kpis"]["listingCount"], 1)
        self.assertIn("tenureHistogram", r["charts"])
        self.assertIn("priceMap", r["charts"])
        self.assertEqual(len(r["charts"]["priceMap"]), 1)
        self.assertIsNotNone(r["kpis"]["priceDeviationIndex"])

    def test_agent_cannot_access_other_agents_property(self):
        with self.assertRaises(Exception):
            get_property_for_user_or_403(self.agent2, self.prop.pk)
        # self.agent can access
        p = get_property_for_user_or_403(self.agent, self.prop.pk)
        self.assertEqual(p.pk, self.prop.pk)
        # admin can access
        p2 = get_property_for_user_or_403(self.admin, self.prop.pk)
        self.assertEqual(p2.pk, self.prop.pk)

    def test_empty_state_no_listings_tasks_followups(self):
        prop2 = Property.objects.create(
            title="Bare",
            internal_code="R4",
            consultant=self.agent,
            property_type=Property.PropertyType.APARTMENT,
            deal_type=Property.DealType.RENT,
            price=Decimal("0"),
            area=0,
            rooms=0,
            address="addr",
            neighborhood="Far",
        )
        r = compute_property_report(prop2)
        self.assertIsNone(r["kpis"]["pricePerSqm"])
        self.assertIsNone(r["kpis"]["priceDeviationIndex"])
        self.assertIsNone(r["kpis"]["listingBurnRate"])
        self.assertEqual(r["kpis"]["tasksOverdueCount"], 0)
        self.assertEqual(r["kpis"]["followupsOverdueCount"], 0)
        self.assertTrue(len(r["warnings"]) >= 1)
        self.assertEqual(r["charts"]["priceMap"], [])


class ReportsAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(
            username="adm2", password="x" * 10, role=UserRole.ADMIN
        )
        self.agent = User.objects.create_user(
            username="ag3", password="x" * 10, role=UserRole.AGENT
        )
        ConsultantProfile.objects.create(
            user=self.agent, full_name="A", branch="B"
        )
        self.prop = Property.objects.create(
            title="P",
            internal_code="M-1",
            consultant=self.agent,
            property_type=Property.PropertyType.APARTMENT,
            deal_type=Property.DealType.SALE,
            price=Decimal("500000000"),
            area=50,
            rooms=1,
            address="addr",
            neighborhood="N",
            latitude=Decimal("35.7"),
            longitude=Decimal("51.4"),
        )

    def test_auth_required(self):
        url = f"/api/reports/properties/{self.prop.pk}/"
        res = self.client.get(url)
        self.assertIn(res.status_code, [401, 403])

    def test_agent_403_on_other_property(self):
        other = User.objects.create_user(
            username="ag4", password="x" * 10, role=UserRole.AGENT
        )
        ConsultantProfile.objects.create(user=other, full_name="X", branch="B")
        self.client.force_authenticate(user=other)
        url = f"/api/reports/properties/{self.prop.pk}/"
        res = self.client.get(url)
        self.assertEqual(res.status_code, 403)

    def test_owner_can_fetch(self):
        self.client.force_authenticate(user=self.agent)
        url = f"/api/reports/properties/{self.prop.pk}/"
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("kpis", data)
        self.assertIn("charts", data)

    def test_csv_export_returns_csv(self):
        self.client.force_authenticate(user=self.agent)
        url = f"/api/reports/properties/{self.prop.pk}/export/"
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "text/csv; charset=utf-8")
        text = res.content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        self.assertEqual(len(rows), 1)
        self.assertIn("شناسه ملک", rows[0])

    def test_csv_export_is_logged_in_activity(self):
        from apps.activity.models import ActivityLog

        self.client.force_authenticate(user=self.agent)
        url = f"/api/reports/properties/{self.prop.pk}/export/"
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)

        entry = ActivityLog.objects.filter(
            action=ActivityLog.ActionType.EXPORT,
            target_type=ActivityLog.TargetType.PROPERTY,
            target_id=self.prop.pk,
        ).first()
        self.assertIsNotNone(entry, "CSV export must be recorded in the activity log")
        self.assertEqual(entry.user_id, self.agent.id)
        self.assertEqual(entry.metadata.get("format"), "csv")

    def test_scope_report_returns_metrics(self):
        self.client.force_authenticate(user=self.agent)
        res = self.client.get("/api/reports/scope/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["kpis"]["propertyCount"], 1)


class PropertyReportPdfExportTests(TestCase):
    """PDF export of the full property report.

    Access: admins may export any property; consultants may only export the
    properties they are assigned to or that are shared with them. Every
    export must be recorded in the activity log like the CSV one.
    """

    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(
            username="pdfadm", password="x" * 10, role=UserRole.ADMIN
        )
        self.agent = User.objects.create_user(
            username="pdfag1", password="x" * 10, role=UserRole.AGENT,
            first_name="Sara", last_name="A",
        )
        ConsultantProfile.objects.create(user=self.agent, full_name="Sara A", branch="B")
        self.stranger = User.objects.create_user(
            username="pdfag2", password="x" * 10, role=UserRole.AGENT
        )
        ConsultantProfile.objects.create(user=self.stranger, full_name="Ali B", branch="B")
        self.prop = Property.objects.create(
            title="Apt",
            internal_code="P-1",
            consultant=self.agent,
            property_type=Property.PropertyType.APARTMENT,
            deal_type=Property.DealType.SALE,
            area=100,
            rooms=2,
            address="addr",
            neighborhood="N",
            latitude=Decimal("35.7"),
            longitude=Decimal("51.4"),
        )
        self.shared = Property.objects.create(
            title="Villa shared",
            internal_code="P-2",
            consultant=self.agent,
            property_type=Property.PropertyType.VILLA,
            deal_type=Property.DealType.SALE,
            area=300,
            address="addr2",
            neighborhood="N2",
            is_shared=True,
        )

    @property
    def url(self):
        return f"/api/reports/properties/{self.prop.pk}/export-pdf/"

    def test_admin_can_export_pdf(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "application/pdf")
        self.assertIn("property-report-", res["Content-Disposition"])
        self.assertTrue(res.content.startswith(b"%PDF-"))
        self.assertGreater(len(res.content), 1000)

    def test_owner_consultant_can_export_pdf(self):
        self.client.force_authenticate(user=self.agent)
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.content.startswith(b"%PDF-"))

    def test_shared_property_exportable_by_other_consultant(self):
        self.client.force_authenticate(user=self.stranger)
        res = self.client.get(f"/api/reports/properties/{self.shared.pk}/export-pdf/")
        self.assertEqual(res.status_code, 200, res.content[:200])
        self.assertTrue(res.content.startswith(b"%PDF-"))

    def test_stranger_cannot_export_non_shared(self):
        self.client.force_authenticate(user=self.stranger)
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 403)
        self.assertFalse(
            ActivityLog.objects.filter(
                action=ActivityLog.ActionType.EXPORT,
                target_type=ActivityLog.TargetType.PROPERTY,
                target_id=self.prop.pk,
            ).exists(),
            "a denied export must not be logged",
        )

    def test_anonymous_cannot_export(self):
        res = self.client.get(self.url)
        self.assertIn(res.status_code, [401, 403])

    def test_unknown_property_is_404(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.get("/api/reports/properties/999999/export-pdf/")
        self.assertEqual(res.status_code, 404)

    def test_pdf_export_is_logged_in_activity(self):
        self.client.force_authenticate(user=self.agent)
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200)

        entry = ActivityLog.objects.filter(
            action=ActivityLog.ActionType.EXPORT,
            target_type=ActivityLog.TargetType.PROPERTY,
            target_id=self.prop.pk,
        ).first()
        self.assertIsNotNone(entry, "PDF export must be recorded in the activity log")
        self.assertEqual(entry.user_id, self.agent.id)
        self.assertEqual(entry.metadata.get("format"), "pdf")


# ---------------------------------------------------------------------------
#  Stage 9 — property PDF: empty-failure hardening + AI section + log tables
# ---------------------------------------------------------------------------

def _pdf_pages(data: bytes) -> int:
    """Number of pages via the PDF page-tree ``/Count`` marker."""
    match = re.search(rb"/Count\s+(\d+)", data)
    return int(match.group(1)) if match else 0


class PropertyPdfContentTests(TestCase):
    """A fully-populated property renders a valid multi-page PDF."""

    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(
            username="pdfc-adm", password="x" * 10, role=UserRole.ADMIN
        )
        self.agent = User.objects.create_user(
            username="pdfc-ag", password="x" * 10, role=UserRole.AGENT,
            first_name="Sara", last_name="A",
        )
        ConsultantProfile.objects.create(user=self.agent, full_name="Sara A", branch="B")
        self.prop = Property.objects.create(
            title="Populated",
            internal_code="POP-1",
            consultant=self.agent,
            property_type=Property.PropertyType.APARTMENT,
            deal_type=Property.DealType.SALE,
            price=Decimal("2000000000"),
            area=120,
            rooms=3,
            address="addr",
            neighborhood="N",
            latitude=Decimal("35.7"),
            longitude=Decimal("51.4"),
        )
        Listing.objects.create(
            property=self.prop, title="آگهی اصلی",
            publish_channel=Listing.PublishChannel.WEBSITE,
            created_by=self.agent, assigned_to=self.agent,
            start_date=timezone.now() - datetime.timedelta(days=5),
            sale_price=Decimal("2000000000"),
        )
        Task.objects.create(
            title="بازدید مشتری", assigned_to=self.agent, created_by=self.agent,
            property=self.prop, due_date=datetime.date.today() + datetime.timedelta(days=2),
            task_type=Task.TaskType.VIEWING, status=Task.Status.PENDING,
        )
        FollowUp.objects.create(
            title="پیگیری اول", consultant=self.agent, contact_name="مشتری",
            property=self.prop, probability=60,
            scheduled_at=timezone.now() - datetime.timedelta(days=2),
        )
        ActivityLog.objects.create(
            user=self.agent, action=ActivityLog.ActionType.CREATE,
            target_type=ActivityLog.TargetType.PROPERTY, target_id=self.prop.pk,
            description="ملک ایجاد شد",
        )

    def test_populated_property_renders_a_valid_multipage_pdf(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.get(f"/api/reports/properties/{self.prop.pk}/export-pdf/")
        self.assertEqual(res.status_code, 200, res.content[:200])
        self.assertEqual(res["Content-Type"], "application/pdf")
        self.assertTrue(res.content.startswith(b"%PDF-"))
        self.assertGreaterEqual(_pdf_pages(res.content), 2)
        self.assertGreater(len(res.content), 10_000)


class PropertyPdfEmptyHistoryTests(TestCase):
    """A property with no history still exports, with a per-table placeholder."""

    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(
            username="pdfe-adm", password="x" * 10, role=UserRole.ADMIN
        )
        self.agent = User.objects.create_user(
            username="pdfe-ag", password="x" * 10, role=UserRole.AGENT,
            first_name="E", last_name="A",
        )
        ConsultantProfile.objects.create(user=self.agent, full_name="E A", branch="B")
        self.prop = Property.objects.create(
            title="Empty",
            internal_code="EMP-1",
            consultant=self.agent,
            property_type=Property.PropertyType.APARTMENT,
            deal_type=Property.DealType.SALE,
            area=80,
            rooms=2,
            address="addr",
            neighborhood="N",
        )

    def test_empty_property_still_exports_a_valid_pdf(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.get(f"/api/reports/properties/{self.prop.pk}/export-pdf/")
        self.assertEqual(res.status_code, 200, res.content[:200])
        self.assertTrue(res.content.startswith(b"%PDF-"))
        self.assertGreater(len(res.content), 1000)

    def test_every_history_table_renders_its_placeholder(self):
        """The four entity tables each emit their «no data» placeholder.

        Extracting reshaped RTL text from a PDF is unreliable, so assert at the
        story level: each section function must append its placeholder
        paragraph to the story when the property has no rows.

        A freshly-created property carries one activity event (its own
        «created» log, written by the post_save signal), so that row is cleared
        here to exercise the logs-table placeholder too.
        """
        ActivityLog.objects.filter(
            target_type=ActivityLog.TargetType.PROPERTY, target_id=self.prop.pk
        ).delete()

        story = []
        styles = _styles()
        _listings_section(story, styles, self.prop)
        _tasks_section(story, styles, self.prop)
        _followups_section(story, styles, self.prop)
        _logs_section(story, styles, self.prop)

        rendered = [flow.text for flow in story if hasattr(flow, "text")]
        for placeholder in (
            "برای این ملک آگهی‌ای ثبت نشده است.",
            "برای این ملک وظیفه‌ای ثبت نشده است.",
            "برای این ملک پیگیری‌ای ثبت نشده است.",
            "لاگ ثبت‌شده‌ای برای این ملک یافت نشد.",
        ):
            self.assertIn(t(placeholder), rendered)


class PropertyPdfAiSectionTests(TestCase):
    """The AI section appears when available and is omitted otherwise —
    the export never fails or hangs because of AI."""

    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(
            username="pdfai-adm", password="x" * 10, role=UserRole.ADMIN
        )
        self.agent = User.objects.create_user(
            username="pdfai-ag", password="x" * 10, role=UserRole.AGENT,
            first_name="A", last_name="I",
        )
        ConsultantProfile.objects.create(user=self.agent, full_name="A I", branch="B")
        self.prop = Property.objects.create(
            title="AI Prop",
            internal_code="AI-1",
            consultant=self.agent,
            property_type=Property.PropertyType.APARTMENT,
            deal_type=Property.DealType.SALE,
            area=100,
            rooms=2,
            address="addr",
            neighborhood="N",
        )

    def _export(self):
        self.client.force_authenticate(user=self.admin)
        return self.client.get(f"/api/reports/properties/{self.prop.pk}/export-pdf/")

    def _pdf_size(self):
        return len(build_property_pdf(self.prop, cached_property_report(self.prop), self.admin))

    def test_ai_section_is_omitted_when_unconfigured(self):
        # No ai_api_base_url in the test environment → the pipeline raises
        # AIError, which the PDF build swallows; the export still succeeds.
        res = self._export()
        self.assertEqual(res.status_code, 200, res.content[:200])
        self.assertTrue(res.content.startswith(b"%PDF-"))

    def test_ai_section_is_present_when_available(self):
        # Size compared at the build level (no HTTP), because every HTTP export
        # appends a new activity-log row and would confound the delta.
        base_size = self._pdf_size()

        with mock.patch(
            "apps.analytics.ai_service.get_cached_description",
            return_value={
                "positives": [
                    "موقعیت مکانی مناسب و قیمت رقابتی",
                    "دسترسی مناسب به حمل‌ونقل عمومی",
                ],
                "negatives": ["روزهای حضور در بازار نسبتاً زیاد است"],
                "summary": (
                    "این ملک با متراژ مناسب در محله‌ای پویا قرار دارد و شاخص‌های "
                    "تعامل آن بالاتر از میانگین محله است."
                ),
            },
        ):
            enriched_size = self._pdf_size()

        self.assertGreater(enriched_size, base_size)

    def test_ai_failure_does_not_break_the_export(self):
        with mock.patch(
            "apps.analytics.ai_service.get_cached_description",
            side_effect=RuntimeError("provider timeout"),
        ):
            # Failure is swallowed: the export still succeeds and stays a
            # valid PDF, just without the AI section.
            res = self._export()

        self.assertEqual(res.status_code, 200, res.content[:200])
        self.assertTrue(res.content.startswith(b"%PDF-"))


class PropertyPdfFontFailureTests(TestCase):
    """A missing/corrupt font is a clean 500 with a Persian detail — never an
    empty PDF."""

    FONT_REL = Path("static") / "fonts" / "ttf" / "IRAN Rounded.ttf"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.font_path = Path(settings.BASE_DIR) / cls.FONT_REL
        cls._original_bytes = cls.font_path.read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.font_path.write_bytes(cls._original_bytes)
        pdf_mod._font_registered = False
        super().tearDownClass()

    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(
            username="pdff-adm", password="x" * 10, role=UserRole.ADMIN
        )
        self.agent = User.objects.create_user(
            username="pdff-ag", password="x" * 10, role=UserRole.AGENT,
            first_name="F", last_name="A",
        )
        ConsultantProfile.objects.create(user=self.agent, full_name="F A", branch="B")
        self.prop = Property.objects.create(
            title="Font Prop",
            internal_code="FNT-1",
            consultant=self.agent,
            property_type=Property.PropertyType.APARTMENT,
            deal_type=Property.DealType.SALE,
            area=100,
            rooms=2,
            address="addr",
            neighborhood="N",
        )
        # Force the next export to (re)load the font from disk.
        pdf_mod._font_registered = False

    def _export(self):
        self.client.force_authenticate(user=self.admin)
        return self.client.get(f"/api/reports/properties/{self.prop.pk}/export-pdf/")

    def test_missing_font_is_a_clean_500(self):
        self.font_path.unlink()
        try:
            res = self._export()
            self.assertEqual(res.status_code, 500)
            self.assertEqual(res.json()["code"], "report_font_unavailable")
            self.assertIn("فونت", res.json()["detail"])
            self.assertFalse(res.content.startswith(b"%PDF-"))
        finally:
            self.font_path.write_bytes(self._original_bytes)

    def test_corrupted_font_is_a_clean_500(self):
        corrupted = self._original_bytes.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        self.font_path.write_bytes(corrupted)
        try:
            res = self._export()
            self.assertEqual(res.status_code, 500)
            self.assertEqual(res.json()["code"], "report_font_unavailable")
            self.assertIn("فونت", res.json()["detail"])
            self.assertFalse(res.content.startswith(b"%PDF-"))
        finally:
            self.font_path.write_bytes(self._original_bytes)
