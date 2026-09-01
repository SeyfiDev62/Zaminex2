"""Persian display labels for activity-log status/type tokens.

The model ``*_display`` labels are now Persian (``Property.Status.AVAILABLE =
("AVAILABLE", "آماده واگذاری")`` and so on), so ``get_status_display()`` is the
canonical source of truth app-wide. This module remains the **single source of
Persian labels for the activity feed** because the feed still needs to (1) map
raw *codes* to Persian when writing new rows (``status_label``) and (2) rewrite
the raw English codes / legacy English labels stored in *old* rows when
rendering them (``translate_description``) — the latter keeps working even
though the models no longer define those English spellings.

1. **Writing** new descriptions (``apps/activity/signals.py``) so that
   newly-created rows are Persian from the start; and
2. **Rendering** stored descriptions (the activity list endpoint and the PDF
   log section) so that *legacy* rows — written before the label promotion and
   still holding raw English codes / labels — are shown in Persian too.

The *set of codes* is derived from the models' ``TextChoices`` (see
``_choice_tokens``) so a status/type added to a model is recognised
automatically; the legacy English *labels* are an explicit frozen list
(``_LEGACY_EN``) since the models no longer carry them.
"""
from __future__ import annotations

import re

from apps.followups.models import FollowUpStatus, FollowUpType
from apps.listings.models import Listing
from apps.properties.models import Property
from apps.tasks.models import Task

# ---------------------------------------------------------------------------
# Canonical Persian labels (mirrored from the frontend maps).
# ---------------------------------------------------------------------------

_PROPERTY_STATUS_FA = {
    "AVAILABLE": "آماده واگذاری",
    "RESERVED": "رزرو شده",
    "SOLD": "فروخته/واگذارشده",
    "INACTIVE": "بایگانی‌شده",
}

_LISTING_STATUS_FA = {
    "DRAFT": "پیش‌نویس",
    "ACTIVE": "منتشرشده (فعال)",
    "PAUSED": "متوقف‌شده",
    "SOLD": "فروخته‌شده",
    "EXPIRED": "منقضی‌شده",
    "ARCHIVED": "بایگانی‌شده",
}

_TASK_STATUS_FA = {
    "PENDING": "در انتظار انجام",
    "IN_PROGRESS": "در حال انجام",
    "COMPLETED": "تکمیل‌شده",
    "CANCELLED": "لغوشده",
}

_TASK_PRIORITY_FA = {
    "LOW": "اولویت کم",
    "MEDIUM": "اولویت عادی",
    "HIGH": "اولویت بالا",
    "URGENT": "اولویت فوری",
}

_TASK_TYPE_FA = {
    "VIEWING": "بازدید ملک",
    "DOCUMENT": "بررسی مدارک",
    "NEGOTIATION": "مذاکره و نشست",
    "FOLLOW_UP": "پیگیری مستمر",
    "ADMINISTRATIVE": "امور اداری و دفتری",
    "SITE_VISIT": "کارشناسی میدانی",
    "CONTRACT": "عقد قرارداد",
    "INSPECTION": "بازرسی فنی",
}

_FOLLOWUP_TYPE_FA = {
    "Call": "تماس تلفنی",
    "Meeting": "جلسه حضوری",
    "Email": "ارسال پیام/ایمیل",
    "Site Visit": "بازدید میدانی ملک",
}

_FOLLOWUP_STATUS_FA = {
    "scheduled": "برنامه‌ریزی‌شده",
    "completed": "تکمیل‌شده",
}


def _choice_tokens(choices_cls, fa_map: dict[str, str], legacy_en: dict[str, str]) -> dict[str, str]:
    """Map raw code, current display label and legacy English label → Persian.

    Keying on the raw code (what signals historically wrote) and the current
    ``get_status_display()`` label keeps the recognised set drifting with the
    schema; ``legacy_en`` carries the *old* English labels that pre-existing
    rows still store but that the models no longer define (their labels were
    promoted to Persian). Unknown values pass through untouched.
    """
    tokens: dict[str, str] = dict(legacy_en)
    for code, label in choices_cls.choices:
        fa = fa_map.get(code)
        if fa is None:
            continue
        tokens[code] = fa
        tokens[label] = fa
    return tokens


# Legacy English labels of the pre-promotion rows. The models' choice labels
# are now Persian, so the old English spellings (e.g. the listing signal's
# former ``get_status_display()`` → "Active", "Sold") are listed here
# explicitly — frozen history that cannot be re-derived from the schema.
_LEGACY_EN: dict[str, dict[str, str]] = {
    "property": {
        "Available": "آماده واگذاری",
        "Reserved": "رزرو شده",
        "Sold": "فروخته/واگذارشده",
        "Archived": "بایگانی‌شده",
    },
    "listing": {
        "Draft": "پیش‌نویس",
        "Active": "منتشرشده (فعال)",
        "Paused": "متوقف‌شده",
        "Sold": "فروخته‌شده",
        "Expired": "منقضی‌شده",
        "Archived": "بایگانی‌شده",
    },
    "task": {
        "Pending": "در انتظار انجام",
        "In Progress": "در حال انجام",
        "Completed": "تکمیل‌شده",
        "Cancelled": "لغوشده",
        "Low": "اولویت کم",
        "Medium": "اولویت عادی",
        "High": "اولویت بالا",
        "Urgent": "اولویت فوری",
        "Viewing": "بازدید ملک",
        "Document": "بررسی مدارک",
        "Negotiation": "مذاکره و نشست",
        "Follow-Up": "پیگیری مستمر",
        "Administrative": "امور اداری و دفتری",
        "Site Visit": "کارشناسی میدانی",
        "Contract": "عقد قرارداد",
        "Inspection": "بازرسی فنی",
    },
    "followup": {
        "Call": "تماس تلفنی",
        "Meeting": "جلسه حضوری",
        "Email": "ارسال پیام/ایمیل",
        "Site Visit": "بازدید میدانی ملک",
        "Scheduled": "برنامه‌ریزی‌شده",
        "Completed": "تکمیل‌شده",
    },
}


# Per target_type token table (code + label → Persian). Derived from the
# models' choices so the recognised set never drifts from the schema.
_TARGET_TOKENS: dict[str, dict[str, str]] = {
    "property": _choice_tokens(Property.Status, _PROPERTY_STATUS_FA, _LEGACY_EN["property"]),
    "listing": _choice_tokens(Listing.Status, _LISTING_STATUS_FA, _LEGACY_EN["listing"]),
    "task": {
        **_choice_tokens(Task.Status, _TASK_STATUS_FA, _LEGACY_EN["task"]),
        **_choice_tokens(Task.Priority, _TASK_PRIORITY_FA, _LEGACY_EN["task"]),
        **_choice_tokens(Task.TaskType, _TASK_TYPE_FA, _LEGACY_EN["task"]),
    },
    "followup": {
        **_choice_tokens(FollowUpType, _FOLLOWUP_TYPE_FA, _LEGACY_EN["followup"]),
        **_choice_tokens(FollowUpStatus, _FOLLOWUP_STATUS_FA, _LEGACY_EN["followup"]),
    },
}


def status_label(target_type: str, code) -> str:
    """Persian label for a raw status code (new rows); raw value fallback."""
    tokens = _TARGET_TOKENS.get(target_type, {})
    return tokens.get(code, code if code is not None else "")


def translate_description(description: str, target_type: str) -> str:
    """Replace known English status/type tokens in a *stored* description.

    Applied only where the description is rendered (activity list + PDF log
    section) so legacy rows that hold raw English tokens display in Persian.
    Unknown tokens and all data values (titles, codes, names) are left intact.
    """
    if not description:
        return description
    tokens = _TARGET_TOKENS.get(target_type)
    if not tokens:
        return description
    # Longest-first so e.g. "In Progress" matches before any shorter token;
    # word boundaries avoid mangling substrings inside data values.
    pattern = re.compile(
        r"\b(" + "|".join(re.escape(t) for t in sorted(tokens, key=len, reverse=True)) + r")\b"
    )
    return pattern.sub(lambda m: tokens[m.group(1)], description)
