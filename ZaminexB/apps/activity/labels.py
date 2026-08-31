"""Persian display labels for activity-log status/type tokens.

The model ``*_display`` labels are still English (``Property.Status.AVAILABLE =
("AVAILABLE", "Available")`` and so on) — a pre-existing contract that the
frontend maps to Persian itself in ``shared/lib/utils.ts``. There is therefore
no Persian ``get_status_display()`` to reuse, so this module is the **single
source of Persian labels for the activity feed**. It is used in two places:

1. **Writing** new descriptions (``apps/activity/signals.py``) so that
   newly-created rows are Persian from the start; and
2. **Rendering** stored descriptions (the activity list endpoint and the PDF
   log section) so that *legacy* rows — which were written before this fix and
   still hold raw English status codes / labels — are shown in Persian too.

The Persian strings mirror the frontend maps exactly (``toPersianPropertyStatus``,
``toPersianListingStatus``, ``toPersianTaskStatus``, ``toPersianTaskType``,
``toPersianPriority``, ``toPersianFollowupType``), so the feed never disagrees
with the UI. The *set of tokens* is derived from the models' ``TextChoices``
(see ``_choice_tokens``) so a status/type added to a model is recognised
automatically — the map cannot drift from the model.
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


def _choice_tokens(choices_cls, fa_map: dict[str, str]) -> dict[str, str]:
    """Map every raw code **and** English label of ``choices_cls`` to Persian.

    Keying on both the raw code (what signals historically wrote) and the
    ``get_status_display()`` label (what the listing signal wrote) means legacy
    rows in either spelling are recognised. Choices without a Persian entry are
    simply absent, so unknown values pass through untouched.
    """
    tokens: dict[str, str] = {}
    for code, label in choices_cls.choices:
        fa = fa_map.get(code)
        if fa is None:
            continue
        tokens[code] = fa
        tokens[label] = fa
    return tokens


# Per target_type token table (code + label → Persian). Derived from the
# models' choices so the recognised set never drifts from the schema.
_TARGET_TOKENS: dict[str, dict[str, str]] = {
    "property": _choice_tokens(Property.Status, _PROPERTY_STATUS_FA),
    "listing": _choice_tokens(Listing.Status, _LISTING_STATUS_FA),
    "task": {
        **_choice_tokens(Task.Status, _TASK_STATUS_FA),
        **_choice_tokens(Task.Priority, _TASK_PRIORITY_FA),
        **_choice_tokens(Task.TaskType, _TASK_TYPE_FA),
    },
    "followup": {
        **_choice_tokens(FollowUpType, _FOLLOWUP_TYPE_FA),
        **_choice_tokens(FollowUpStatus, _FOLLOWUP_STATUS_FA),
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
