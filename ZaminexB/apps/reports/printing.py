"""Server-side context for the print-ready «گزارش کامل» property report.

The full property report used to be rendered to PDF bytes with reportlab and
downloaded as a file (which sometimes arrived truncated, and never let the
user choose a destination, page range or color). The document is now served
as a print-ready HTML page; the browser's native print system — the dialog
with destination / pages / color controls — turns it into paper or «Save as
PDF». The server's job shrank to *data*: this module builds the exact same
factual content the PDF carried, pre-formatted as display strings so the
template stays a dumb renderer and the tests can assert on plain text.

Sections (same order and titles the PDF used, ۱..۷):

    header — company name, title, generation time, exported by
    ۱. اطلاعات ملک — the property's stored facts
    ۲. شاخص‌های کلیدی — the report KPIs
    ۳. آگهی‌های ملک — every listing with its commercial details
    ۴. وظایف ملک — every task attached to the property
    ۵. پیگیری‌های ملک — every non-archived follow-up
    ۶. نمودارها — a few compact charts from the report data
    ۷. سابقه و لاگ‌های ملک — the activity-log entries of the property

The AI description («توصیف هوش مصنوعی») is deliberately *not* part of this
document — the same policy the PDF followed: the printed report is a factual
record handed to customers and filed, while the on-screen summary is an
internal reading aid. Consequently the build never touches
``apps.analytics.ai_service`` at all.
"""

from __future__ import annotations

import datetime
import math

from django.conf import settings
from django.utils import timezone as dj_timezone

from jdatetime import datetime as jalali_datetime

# The database stores UTC; the agency and its customers live in Iran, so the
# printed times are shown in Tehran time.
try:
    from zoneinfo import ZoneInfo

    _TEHRAN_TZ = ZoneInfo("Asia/Tehran")
except Exception:  # pragma: no cover - tzdata always present in CPython
    _TEHRAN_TZ = datetime.timezone.utc

_PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


# ---------------------------------------------------------------------------
#  Display formatting (Jalali dates, Persian digits, thousand separators)
# ---------------------------------------------------------------------------

def fa_number(value, digits: int = 0) -> str:
    """A number with Persian digits and thousand separators.

    Non-numeric values pass through as plain strings: unlike the old PDF
    (which had to reshape/bidi-flip Arabic-script text for a CJK-oriented
    renderer), the browser renders Persian natively, so there is nothing to
    transform here.
    """
    if value is None:
        return ""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return str(value)
    if math.isnan(n):
        return "—"
    if digits:
        s = f"{n:,.{digits}f}"
    else:
        s = f"{int(round(n)):,}"
    return s.translate(_PERSIAN_DIGITS)


def fa_value(value) -> str:
    """Like ``fa_number``, but keeps a single decimal when one is present."""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return fa_number(value)
    if n == int(n):
        return fa_number(n)
    return fa_number(n, 1)


def _jalali(dt, with_time: bool = False) -> str:
    if dt is None:
        return "—"
    if getattr(settings, "USE_TZ", True) and dt.tzinfo is None:
        dt = dj_timezone.make_aware(dt)
    return jalali_datetime.fromgregorian(
        datetime=dt.astimezone(_TEHRAN_TZ)
    ).strftime("%Y/%m/%d" if not with_time else "%Y/%m/%d %H:%M")


# ---------------------------------------------------------------------------
#  Persian labels (kept consistent with the UI and the old PDF)
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
    "Inspection": "بررسی",
}
TASK_STATUS_FA = {
    "PENDING": "در انتظار انجام",
    "IN_PROGRESS": "در حال انجام",
    "COMPLETED": "تکمیل‌شده",
    "CANCELLED": "لغوشده",
}


def _fa_labels(items: list[dict], table: dict) -> list[dict]:
    """Persian-ize chart labels.

    Chart labels come from the report as display strings; translate the ones
    that are still English (task / follow-up / channel types) so the whole
    document reads in Persian. Case-insensitive: the report mixes raw values
    ("WEBSITE") and display labels ("Website").
    """
    upper = {k.upper(): v for k, v in table.items()}
    out = []
    for item in items:
        label = item.get("label")
        fa_label = table.get(label) or upper.get(str(label).upper()) or label
        out.append({**item, "label": fa_label})
    return out


# ---------------------------------------------------------------------------
#  Sections
# ---------------------------------------------------------------------------

def _property_info_rows(prop) -> list[tuple[str, str]]:
    consultant_name = (
        (prop.consultant.get_full_name() or prop.consultant.username)
        if prop.consultant
        else "—"
    )
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
    return [
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
        ("تعداد تصاویر", fa_number(len(prop.images.all()))),
    ]


def _kpi_rows(kpis: dict) -> list[tuple[str, str]]:
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
        return fa_number(value)

    return [(label, fmt(key, kpis.get(key))) for key, label in labels]


def _listing_rows(prop) -> list[dict]:
    listings = list(prop.listings.all())
    rows = []
    for l in sorted(
        listings,
        key=lambda x: (x.start_date or x.created_at or datetime.datetime.min),
        reverse=True,
    ):
        if l.deal_type_id and l.deal_type.name == "rent" and l.deposit is not None:
            price = (
                f"{fa_number(l.deposit)} رهن / {fa_number(l.monthly_rent)} اجاره"
                if l.monthly_rent
                else f"{fa_number(l.deposit)} رهن"
            )
        elif l.sale_price is not None:
            price = fa_number(l.sale_price)
        else:
            price = "—"
        rows.append(
            {
                "date": _jalali(l.start_date or l.created_at),
                "status": LISTING_STATUS_FA.get(l.status, l.status),
                "price": price,
                "channel": CHANNEL_FA.get(l.publish_channel, l.publish_channel),
                "title": l.title or "—",
            }
        )
    return rows


def _task_rows(prop) -> list[dict]:
    rows = []
    for task in sorted(prop.tasks.all(), key=lambda x: x.due_date or datetime.date.min, reverse=True):
        due = (
            datetime.datetime.combine(task.due_date, datetime.time())
            if task.due_date
            else None
        )
        rows.append(
            {
                "date": _jalali(due),
                "status": TASK_STATUS_FA.get(task.status, task.status),
                "title": task.title or "—",
            }
        )
    return rows


def _followup_rows(prop) -> list[dict]:
    rows = []
    for f in sorted(
        (f for f in prop.followups.all() if not f.is_archived),
        key=lambda x: x.scheduled_at or datetime.datetime.min,
        reverse=True,
    ):
        rows.append(
            {
                "date": _jalali(f.scheduled_at),
                "status": FOLLOWUP_STATUS_FA.get(f.status, f.status),
                "title": f.title or "—",
            }
        )
    return rows


def _chart_blocks(charts: dict) -> list[dict]:
    """The three compact charts the PDF rendered, as horizontal-bar data.

    Each block carries pre-computed bar widths (percent of the chart max) so
    the template needs no arithmetic of its own. A chart whose values are all
    zero renders its «no data» placeholder, exactly like the PDF did.
    """
    specs = [
        (
            "وظایف سررسیدگذشته بر اساس نوع",
            charts.get("tasksOverdueByType") or [],
            "count",
            TASK_TYPE_FA,
            "#F59E0B",
        ),
        (
            "آگهی‌ها بر اساس کانال انتشار",
            charts.get("publishChannel") or [],
            "count",
            CHANNEL_FA,
            "#3B82F6",
        ),
        (
            "نرخ تکمیل پیگیری‌ها بر اساس نوع",
            charts.get("workCompletionByType") or [],
            "rate",
            FOLLOWUP_TYPE_FA,
            "#0BB68A",
        ),
    ]
    blocks = []
    for title, items, key, table, color in specs:
        items = _fa_labels(items, table)
        values = [float(item.get(key) or 0) for item in items]
        max_value = max(values, default=0)
        has_data = max_value > 0
        bars = []
        if has_data:
            for item, value in zip(items, values):
                bars.append(
                    {
                        "label": item.get("label", ""),
                        "value": fa_value(value),
                        "pct": round(value / max_value * 100, 1) if max_value else 0,
                        "color": color,
                    }
                )
        blocks.append({"title": title, "hasData": has_data, "bars": bars})
    return blocks


def _log_rows(prop) -> list[dict]:
    from django.db.models import Q

    from apps.activity.labels import translate_description
    from apps.activity.models import ActivityLog

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
    rows = []
    for e in entries:
        user = (e.user.get_full_name() or e.user.username) if e.user else "سیستم"
        rows.append(
            {
                # Legacy rows hold raw English status codes; the shared
                # translator rewrites them so the printed report matches the
                # live activity feed.
                "description": translate_description(e.description or "—", e.target_type),
                "action": e.get_action_display(),
                "user": user,
                "when": _jalali(e.created_at, with_time=True),
            }
        )
    return rows


# ---------------------------------------------------------------------------
#  Entry point
# ---------------------------------------------------------------------------

def build_print_report_context(prop, report: dict, exported_by) -> dict:
    """Assemble everything the print template renders, as display strings."""
    from apps.common.models import CompanySettings

    company = CompanySettings.get_solo()
    exporter = (exported_by.get_full_name() or exported_by.username) if exported_by else "—"

    return {
        "company_name": company.company_name or "—",
        "property": prop,
        "header_title": f"ملک «{prop.title or '—'}» — کد {prop.internal_code or '—'}",
        "generated_at": _jalali(dj_timezone.now(), with_time=True),
        "exporter": exporter,
        "property_info": _property_info_rows(prop),
        "kpis": _kpi_rows(report.get("kpis") or {}),
        "listings": _listing_rows(prop),
        "tasks": _task_rows(prop),
        "followups": _followup_rows(prop),
        "charts": _chart_blocks(report.get("charts") or {}),
        "logs": _log_rows(prop),
    }
