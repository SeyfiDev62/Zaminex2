"""Server-side PDF rendering for the «گزارش کامل» property report.

One PDF per property, built entirely from the same scoped data the JSON
report uses (``compute_property_report``) plus the property's related
records, so the document always matches what the screen shows:

    1. header  — company name, title, generation time, exported by
    2. اطلاعات ملک — the property's stored facts
    3. شاخص‌های کلیدی — the report KPIs
    4. آگهی‌ها — every listing of the property with its commercial details
    5. پیگیری‌ها — every active follow-up
    6. نمودارها — a few compact charts from the report data
    7. سابقه و لاگ‌ها — every activity-log entry that belongs to this
       property (its own events plus the events of its listings, follow-ups
       and tasks)

Persian text is reshaped (arabic_reshaper) and bidi-flipped (python-bidi)
before it reaches reportlab, and the project's own IRAN Rounded font is
registered, so the document renders exactly like the UI.
"""

from __future__ import annotations

import datetime
import math
from decimal import Decimal
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.utils import timezone as dj_timezone

import arabic_reshaper
from bidi.algorithm import get_display
from jdatetime import datetime as jalali_datetime
from reportlab.graphics import renderPDF
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

FONT_NAME = "IRANRounded"
_font_registered = False

# The database stores UTC; the agency and its customers live in Iran, so the
# printed times are shown in Tehran time.
try:
    from zoneinfo import ZoneInfo

    _TEHRAN_TZ = ZoneInfo("Asia/Tehran")
except Exception:  # pragma: no cover - tzdata always present in CPython
    _TEHRAN_TZ = datetime.timezone.utc

_PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def _has_persian(text: str) -> bool:
    return any("\u0600" <= ch <= "\u06FF" or ch == "\u200c" for ch in text)


def t(value) -> str:
    """A display string for reportlab: reshaped + bidi-flipped when the text
    contains Arabic-script characters; pure ASCII (codes, coordinates) is
    passed through untouched."""
    if value is None:
        return ""
    s = str(value)
    if not s.strip():
        return ""
    if _has_persian(s):
        return get_display(arabic_reshaper.reshape(s))
    return s


def fa_number(value, digits: int = 0) -> str:
    """A number with Persian digits and thousand separators."""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return t(value)
    if math.isnan(n):
        return "—"
    if digits:
        s = f"{n:,.{digits}f}"
    else:
        s = f"{int(round(n)):,}"
    return s.translate(_PERSIAN_DIGITS)


def _jalali(dt, with_time: bool = False) -> str:
    if dt is None:
        return "—"
    if getattr(settings, "USE_TZ", True) and dt.tzinfo is None:
        dt = dj_timezone.make_aware(dt)
    return jalali_datetime.fromgregorian(
        datetime=dt.astimezone(_TEHRAN_TZ)
    ).strftime("%Y/%m/%d" if not with_time else "%Y/%m/%d %H:%M")


# ---------------------------------------------------------------------------
#  Persian labels (kept consistent with the UI)
# ---------------------------------------------------------------------------

PROPERTY_STATUS_FA = {
    "AVAILABLE": "در دسترس",
    "RESERVED": "رزرو شده",
    "SOLD": "فروخته شد",
    "INACTIVE": "بایگانی",
}
LISTING_STATUS_FA = {
    "DRAFT": "پیش‌نویس",
    "ACTIVE": "فعال",
    "PAUSED": "متوقف",
    "SOLD": "فروخته شد",
    "EXPIRED": "منقضی شده",
    "ARCHIVED": "بایگانی",
}
CHANNEL_FA = {
    "WEBSITE": "وب‌سایت",
    "INSTAGRAM": "اینستاگرام",
    "TELEGRAM": "تلگرام",
    "OTHER": "سایر",
}
DEAL_TYPE_FA = {"SALE": "فروش", "RENT": "اجاره"}
LEGACY_TYPE_FA = {
    "APARTMENT": "آپارتمان",
    "VILLA": "ویلا",
    "TOWNHOUSE": "تاون‌هاوس",
    "STUDIO": "استودیو",
    "PENTHOUSE": "پنت‌هاوس",
    "COMMERCIAL": "تجاری",
    "OFFICE": "اداری",
    "SHOP": "مغازه",
    "LAND": "زمین",
    "OTHER": "سایر",
}
FOLLOWUP_STATUS_FA = {"scheduled": "زمان‌بندی‌شده", "completed": "تکمیل‌شده"}
FOLLOWUP_TYPE_FA = {
    "Call": "تماس تلفنی",
    "Meeting": "جلسه حضوری",
    "Email": "ایمیل / پیام",
    "Site Visit": "بازدید میدانی",
}
TASK_TYPE_FA = {
    "Viewing": "بازدید",
    "Document": "اسناد",
    "Negotiation": "مذاکره",
    "Follow-Up": "پیگیری",
    "Administrative": "اداری",
    "Site Visit": "بازدید میدانی",
    "Contract": "قرارداد",
    "Inspection": "بازرسی",
}


def _register_font() -> None:
    global _font_registered
    if _font_registered:
        return
    path = Path(settings.BASE_DIR) / "static" / "fonts" / "ttf" / "IRAN Rounded.ttf"
    pdfmetrics.registerFont(TTFont(FONT_NAME, str(path)))
    _font_registered = True


# ---------------------------------------------------------------------------
#  Styles
# ---------------------------------------------------------------------------

def _styles() -> dict:
    base = dict(fontName=FONT_NAME, wordWrap="RTL")
    return {
        "title": ParagraphStyle("title", leading=20, **base, fontSize=15, textColor=colors.HexColor("#0F172A")),
        "subtitle": ParagraphStyle("subtitle", leading=14, **base, fontSize=10, textColor=colors.HexColor("#475569")),
        "meta": ParagraphStyle("meta", leading=12, **base, fontSize=8.5, textColor=colors.HexColor("#64748B")),
        "section": ParagraphStyle("section", leading=16, **base, fontSize=11.5, textColor=colors.HexColor("#0F172A")),
        "cell": ParagraphStyle("cell", leading=11, **base, fontSize=8),
        "cellCenter": ParagraphStyle("cellCenter", leading=11, **base, fontSize=8, alignment=TA_CENTER),
        "cellLabel": ParagraphStyle("cellLabel", leading=11, **base, fontSize=8, textColor=colors.HexColor("#475569")),
        "empty": ParagraphStyle("empty", leading=12, **base, fontSize=8.5, textColor=colors.HexColor("#94A3B8"), alignment=TA_CENTER),
    }


GRID_COLOR = colors.HexColor("#E2E8F0")
HEADER_BG = colors.HexColor("#F1F5F9")
ACCENT = colors.HexColor("#0BB68A")


def _section_header(story, styles, text) -> None:
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(t(text), styles["section"]))
    story.append(
        Table(
            [[""]],
            colWidths=[178 * mm],
            rowHeights=[0.6 * mm],
            style=TableStyle([("BACKGROUND", (0, 0), (-1, -1), ACCENT)]),
        )
    )
    story.append(Spacer(1, 2.5 * mm))


def _label_value_table(story, styles, rows: list[tuple[str, str]]) -> None:
    data = [[Paragraph(t(v), styles["cell"]), Paragraph(t(k), styles["cellLabel"])] for k, v in rows]
    table = Table(data, colWidths=[118 * mm, 60 * mm], hAlign="RIGHT")
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, GRID_COLOR),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, colors.HexColor("#FAFAFA")]),
            ]
        )
    )
    story.append(table)


def _bar_chart(data: list[dict], value_key: str, color: str, height: float = 52 * mm) -> Drawing | None:
    """A compact RTL bar chart. Categories run right→left (first item on the
    right), matching Persian reading order."""
    labels = [d.get("label", "") for d in data]
    values = [float(d.get(value_key, 0) or 0) for d in data]
    if not labels or not any(v > 0 for v in values):
        return None

    reversed_labels = [t(l) for l in reversed(labels)]
    reversed_values = list(reversed(values))

    width = 178 * mm
    drawing = Drawing(width, height)
    chart = VerticalBarChart()
    chart.x, chart.y = 28 * mm, 8 * mm
    chart.width, chart.height = width - 40 * mm, height - 20 * mm
    chart.data = [reversed_values]
    chart.categoryAxis.categoryNames = reversed_labels
    chart.categoryAxis.labels.fontName = FONT_NAME
    chart.categoryAxis.labels.fontSize = 7.5
    chart.categoryAxis.labels.dy = -2
    chart.valueAxis.valueMin = 0
    chart.valueAxis.valueMax = max(math.ceil(max(reversed_values) / 4.0) * 4, 1)
    chart.valueAxis.valueStep = max(math.ceil(max(reversed_values) / 4.0), 1)
    chart.valueAxis.labels.fontName = FONT_NAME
    chart.valueAxis.labels.fontSize = 7
    chart.bars[0].fillColor = colors.HexColor(color)
    chart.bars[0].strokeColor = None
    chart.barLabels.fontName = FONT_NAME
    chart.barLabels.fontSize = 7
    drawing.add(chart)
    return drawing


def _chart_block(story, styles, title: str, drawing: Drawing | None) -> None:
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(t(title), styles["meta"]))
    if drawing is None:
        story.append(Paragraph(t("داده‌ای برای نمایش وجود ندارد."), styles["empty"]))
        return
    story.append(drawing)


# ---------------------------------------------------------------------------
#  Sections
# ---------------------------------------------------------------------------

def _property_info_section(story, styles, prop) -> None:
    _section_header(story, styles, "۱. اطلاعات ملک")
    consultant_name = (prop.consultant.get_full_name() or prop.consultant.username) if prop.consultant else "—"
    type_name = (
        prop.property_type_ref.display_name
        if prop.property_type_ref
        else LEGACY_TYPE_FA.get(prop.property_type, prop.property_type or "—")
    )
    usage_name = prop.property_usage.display_name if prop.property_usage else "—"
    location = prop.district.full_path if prop.district_id else (prop.neighborhood or "—")
    coords = (
        f"{prop.latitude:.6f}، {prop.longitude:.6f}"
        if prop.latitude is not None and prop.longitude is not None
        else "—"
    )
    owner_name = " ".join(x for x in (prop.owner_first_name, prop.owner_last_name) if x) or "—"
    owner = f"{owner_name} — {prop.owner_phone}" if prop.owner_phone else owner_name
    rows = [
        ("عنوان", prop.title or "—"),
        ("کد داخلی", prop.internal_code or "—"),
        ("وضعیت", PROPERTY_STATUS_FA.get(prop.status, prop.status)),
        ("نوع ملک", type_name),
        ("کاربری", usage_name),
        ("نوع معامله", DEAL_TYPE_FA.get(prop.deal_type, prop.deal_type or "—")),
        ("محله / منطقه", location),
        ("آدرس کامل", prop.address or "—"),
        ("مساحت", f"{fa_number(prop.area)} متر مربع" if prop.area else "—"),
        ("تعداد خواب", fa_number(prop.rooms) if prop.rooms else "—"),
        ("طبقه", fa_number(prop.floor) if prop.floor else "—"),
        ("سال ساخت", fa_number(prop.built_year) if prop.built_year else "—"),
        ("مختصات جغرافیایی", coords),
        ("مشاور مسئول", consultant_name),
        ("مالک", owner),
        ("تعداد تصاویر", fa_number(prop.images.count())),
    ]
    _label_value_table(story, styles, rows)


def _kpi_section(story, styles, kpis: dict) -> None:
    _section_header(story, styles, "۲. شاخص‌های کلیدی")
    labels = [
        ("tenureDays", "روزهای تصدی"),
        ("daysOnMarket", "روز در بازار"),
        ("tasksOverdueCount", "وظایف سررسیدگذشته"),
        ("followupsOverdueCount", "پیگیری‌های سررسیدگذشته"),
        ("workCompletionRate", "نرخ تکمیل کار"),
        ("listingCount", "تعداد آگهی‌ها"),
        ("followupCount", "تعداد پیگیری‌ها"),
        ("imagesCount", "تعداد تصاویر"),
        ("pricePerSqm", "قیمت هر متر (تومان)"),
        ("priceDeviationIndex", "انحراف قیمت از عرف محله"),
        ("engagementHeatScore", "امتیاز تعامل"),
        ("listingBurnRate", "نرخ اتلاف آگهی"),
    ]

    def fmt(key: str, value) -> str:
        if value is None:
            return "—"
        # workCompletionRate already arrives as a percentage (the service
        # multiplies by 100); listingBurnRate is a 0..1 fraction.
        if key == "workCompletionRate":
            return f"{fa_number(value, 1)}٪"
        if key == "listingBurnRate":
            return f"{fa_number(value * 100)}٪"
        if key == "priceDeviationIndex":
            sign = "+" if value >= 0 else "−"
            return f"{sign}{fa_number(abs(value) * 100)}٪"
        if key == "pricePerSqm":
            return fa_number(value)
        return fa_number(value)

    rows = [(label, fmt(key, kpis.get(key))) for key, label in labels]
    _label_value_table(story, styles, rows)


def _listings_section(story, styles, prop) -> None:
    _section_header(story, styles, "۳. آگهی‌های ملک")
    listings = list(prop.listings.all())
    if not listings:
        story.append(Paragraph(t("برای این ملک آگهی‌ای ثبت نشده است."), styles["empty"]))
        return
    header = [t(h) for h in ["پایان", "شروع", "قیمت (تومان)", "وضعیت", "کانال", "عنوان"]]
    data = [header]
    for l in sorted(listings, key=lambda x: (x.start_date or x.created_at or datetime.datetime.min), reverse=True):
        if l.deal_type_id and l.deal_type.name == "rent" and l.deposit is not None:
            price = f"{fa_number(l.deposit)} رهن / {fa_number(l.monthly_rent)} اجاره" if l.monthly_rent else f"{fa_number(l.deposit)} رهن"
        elif l.sale_price is not None:
            price = fa_number(l.sale_price)
        else:
            price = "—"
        data.append(
            [
                Paragraph(t(_jalali(l.end_date)), styles["cellCenter"]),
                Paragraph(t(_jalali(l.start_date)), styles["cellCenter"]),
                Paragraph(t(price), styles["cellCenter"]),
                Paragraph(t(LISTING_STATUS_FA.get(l.status, l.status)), styles["cellCenter"]),
                Paragraph(t(CHANNEL_FA.get(l.publish_channel, l.publish_channel)), styles["cellCenter"]),
                Paragraph(t(l.title or "—"), styles["cell"]),
            ]
        )
    table = Table(data, colWidths=[22 * mm, 22 * mm, 40 * mm, 26 * mm, 28 * mm, 40 * mm], hAlign="RIGHT")
    table.setStyle(_table_style(len(data)))
    story.append(table)


def _followups_section(story, styles, prop) -> None:
    _section_header(story, styles, "۴. پیگیری‌های ملک")
    followups = [f for f in prop.followups.all() if not f.is_archived]
    if not followups:
        story.append(Paragraph(t("برای این ملک پیگیری‌ای ثبت نشده است."), styles["empty"]))
        return
    header = [t(h) for h in ["احتمال", "زمان", "وضعیت", "نوع", "مخاطب", "عنوان"]]
    data = [header]
    for f in sorted(followups, key=lambda x: x.scheduled_at or datetime.datetime.min, reverse=True):
        data.append(
            [
                Paragraph(t(f"{fa_number(f.probability)}٪") if f.probability is not None else t("—"), styles["cellCenter"]),
                Paragraph(t(_jalali(f.scheduled_at)), styles["cellCenter"]),
                Paragraph(t(FOLLOWUP_STATUS_FA.get(f.status, f.status)), styles["cellCenter"]),
                Paragraph(t(FOLLOWUP_TYPE_FA.get(f.follow_up_type, f.get_follow_up_type_display())), styles["cellCenter"]),
                Paragraph(t(f.contact_name or "—"), styles["cellCenter"]),
                Paragraph(t(f.title or "—"), styles["cell"]),
            ]
        )
    table = Table(data, colWidths=[18 * mm, 24 * mm, 26 * mm, 28 * mm, 36 * mm, 46 * mm], hAlign="RIGHT")
    table.setStyle(_table_style(len(data)))
    story.append(table)


def _charts_section(story, styles, charts: dict) -> None:
    _section_header(story, styles, "۵. نمودارها")
    # Chart labels come from the report as display strings; translate the ones
    # that are still English (task / follow-up / channel types) so the whole
    # document reads in Persian. Case-insensitive: the report mixes raw values
    # ("WEBSITE") and display labels ("Website").
    def _fa_labels(items: list[dict], table: dict) -> list[dict]:
        upper = {k.upper(): v for k, v in table.items()}
        out = []
        for item in items:
            label = item.get("label")
            fa_label = table.get(label) or upper.get(str(label).upper()) or label
            out.append({**item, "label": fa_label})
        return out

    specs = [
        ("وظایف سررسیدگذشته بر اساس نوع", _fa_labels(charts.get("tasksOverdueByType") or [], TASK_TYPE_FA), "count", "#F59E0B"),
        ("آگهی‌ها بر اساس کانال انتشار", _fa_labels(charts.get("publishChannel") or [], CHANNEL_FA), "count", "#3B82F6"),
        ("نرخ تکمیل پیگیری‌ها بر اساس نوع", _fa_labels(charts.get("workCompletionByType") or [], FOLLOWUP_TYPE_FA), "rate", "#0BB68A"),
    ]
    for title, data, key, color in specs:
        _chart_block(story, styles, title, _bar_chart(data, key, color))


def _logs_section(story, styles, prop) -> None:
    from django.db.models import Q

    from apps.activity.models import ActivityLog

    _section_header(story, styles, "۶. سابقه و لاگ‌های ملک")
    listing_ids = list(prop.listings.values_list("id", flat=True))
    followup_ids = list(prop.followups.values_list("id", flat=True))
    task_ids = list(prop.tasks.values_list("id", flat=True))
    entries = (
        ActivityLog.objects.filter(
            (Q(target_type="property", target_id=prop.id))
            | (Q(target_type="listing", target_id__in=listing_ids))
            | (Q(target_type="followup", target_id__in=followup_ids))
            | (Q(target_type="task", target_id__in=task_ids))
        )
        .select_related("user")
        .order_by("-created_at", "-id")[:200]
    )
    if not entries.exists():
        story.append(Paragraph(t("لاگ ثبت‌شده‌ای برای این ملک یافت نشد."), styles["empty"]))
        return
    header = [t(h) for h in ["شرح", "عملیات", "کاربر", "تاریخ و ساعت"]]
    data = [header]
    for e in entries:
        user = (e.user.get_full_name() or e.user.username) if e.user else "سیستم"
        data.append(
            [
                Paragraph(t(e.description or "—"), styles["cell"]),
                Paragraph(t(e.get_action_display()), styles["cellCenter"]),
                Paragraph(t(user), styles["cellCenter"]),
                Paragraph(t(_jalali(e.created_at, with_time=True)), styles["cellCenter"]),
            ]
        )
    table = Table(data, colWidths=[92 * mm, 26 * mm, 32 * mm, 28 * mm], hAlign="RIGHT")
    table.setStyle(_table_style(len(data)))
    story.append(table)


def _table_style(n_rows: int) -> TableStyle:
    return TableStyle(
        [
            ("FONTNAME", (0, 0), (-1, -1), FONT_NAME),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.25, GRID_COLOR),
            ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#FAFAFA")]),
        ]
    )


def _header_and_footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont(FONT_NAME, 7.5)
    canvas.setFillColor(colors.HexColor("#94A3B8"))
    canvas.drawRightString(doc.pagesize[0] - doc.rightMargin, 8 * mm, f"صفحه {fa_number(doc.page)}")
    canvas.drawString(doc.leftMargin, 8 * mm, "ساخته‌شده توسط CRM زمینکس")
    canvas.setStrokeColor(GRID_COLOR)
    canvas.setLineWidth(0.4)
    canvas.line(doc.leftMargin, 11 * mm, doc.pagesize[0] - doc.rightMargin, 11 * mm)
    canvas.restoreState()


# ---------------------------------------------------------------------------
#  Entry point
# ---------------------------------------------------------------------------

def build_property_pdf(prop, report: dict, exported_by) -> bytes:
    """Render the full property report to PDF bytes."""
    _register_font()
    styles = _styles()
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=t(f"گزارش کامل ملک — {prop.title}"),
        author="Zaminex",
    )

    from apps.common.models import CompanySettings

    story = []
    company = CompanySettings.get_solo()
    story.append(Paragraph(t(company.company_name), styles["meta"]))
    story.append(Spacer(1, 1.5 * mm))
    story.append(Paragraph(t("گزارش کامل ملک"), styles["title"]))
    story.append(Spacer(1, 1 * mm))
    story.append(
        Paragraph(
            t(f"ملک «{prop.title or '—'}» — کد {prop.internal_code or '—'}"),
            styles["subtitle"],
        )
    )
    exporter = (exported_by.get_full_name() or exported_by.username) if exported_by else "—"
    story.append(
        Paragraph(
            t(f"تاریخ تهیه: {_jalali(dj_timezone.now(), with_time=True)}   |   تهیه‌کننده: {exporter}"),
            styles["meta"],
        )
    )
    story.append(Spacer(1, 2 * mm))
    story.append(
        Table(
            [[""]],
            colWidths=[178 * mm],
            rowHeights=[0.8 * mm],
            style=TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#0F172A"))]),
        )
    )

    _property_info_section(story, styles, prop)
    _kpi_section(story, styles, report.get("kpis") or {})
    _listings_section(story, styles, prop)
    _followups_section(story, styles, prop)
    _charts_section(story, styles, report.get("charts") or {})
    _logs_section(story, styles, prop)

    doc.build(story, onFirstPage=_header_and_footer, onLaterPages=_header_and_footer)
    return buf.getvalue()
