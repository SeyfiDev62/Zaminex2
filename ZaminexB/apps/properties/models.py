import os
import uuid

from django.db import models, transaction
from django.db.models import BigIntegerField, Max
from django.db.models.functions import Cast, Substr
from django.db.utils import IntegrityError

from .validators import validate_appraisal_pdf, validate_property_image
from django.conf import settings

from apps.common.attribute_values import BaseAttributeValue


class ActivePropertyManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().exclude(status=Property.Status.INACTIVE)


class Property(models.Model):
    class Status(models.TextChoices):
        AVAILABLE = "AVAILABLE", "آماده واگذاری"
        RESERVED = "RESERVED", "رزرو شده"
        SOLD = "SOLD", "فروخته/واگذارشده"
        INACTIVE = "INACTIVE", "بایگانی‌شده"

    class DealType(models.TextChoices):
        SALE = "SALE", "فروش"
        RENT = "RENT", "اجاره"

    class PropertyType(models.TextChoices):
        APARTMENT = "APARTMENT", "آپارتمان"
        VILLA = "VILLA", "ویلا"
        TOWNHOUSE = "TOWNHOUSE", "خانه ویلایی"
        STUDIO = "STUDIO", "استودیو"
        PENTHOUSE = "PENTHOUSE", "پنت‌هاوس"
        COMMERCIAL = "COMMERCIAL", "تجاری/اداری"
        OFFICE = "OFFICE", "دفتر کار"
        SHOP = "SHOP", "مغازه"
        LAND = "LAND", "زمین"
        OTHER = "OTHER", "سایر"

    title = models.CharField(max_length=255, verbose_name="عنوان ملک")
    internal_code = models.CharField(max_length=50, unique=True, verbose_name="کد داخلی")

    consultant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="properties",
        limit_choices_to={"role": "AGENT"},
        verbose_name="مشاور مسئول",
    )

    # Legacy hard-coded column. Superseded by `property_type_ref` below and
    # removed once every reader has been migrated; kept in place for now so
    # this phase changes no behaviour. See apps/basics/models.py.
    property_type = models.CharField(
        max_length=20,
        choices=PropertyType.choices,
        verbose_name="نوع ملک (قدیمی)",
    )

    # --- reference data (phase 2) ------------------------------------------
    # Nullable during the transition: existing rows are backfilled by the
    # `link_properties_to_basics` command, and the columns above stay
    # authoritative until phase 3 switches the readers over.
    property_usage = models.ForeignKey(
        "basics.PropertyUsage",
        on_delete=models.PROTECT,
        related_name="properties",
        null=True,
        blank=True,
        verbose_name="کاربری ملک",
    )
    property_type_ref = models.ForeignKey(
        "basics.PropertyType",
        on_delete=models.PROTECT,
        related_name="properties",
        null=True,
        blank=True,
        verbose_name="نوع ملک",
    )

    deal_type = models.CharField(
        max_length=20,
        choices=DealType.choices,
        verbose_name="نوع معامله",
    )

    # Deprecated. Pricing belongs to the listing (Listing.sale_price / deposit
    # / monthly_rent) because one property can be advertised for sale and for
    # rent at once.
    #
    # Nothing reads this column directly any more: every caller goes through
    # `apps.analytics.metrics.effective_sale_price`, which prefers the property's
    # sale listings and only falls back here for records created before the
    # split. It is retained so those historical figures stay readable, and can
    # be dropped once no row relies on the fallback.
    price = models.DecimalField(
        max_digits=18,
        decimal_places=0,
        null=True,
        blank=True,
        verbose_name="قیمت (منسوخ — در آگهی ثبت می‌شود)",
    )
    area = models.PositiveIntegerField(verbose_name="مساحت")
    rooms = models.PositiveIntegerField(default=0, verbose_name="تعداد خواب")
    floor = models.IntegerField(null=True, blank=True, verbose_name="طبقه")
    built_year = models.PositiveIntegerField(null=True, blank=True, verbose_name="سال ساخت")

    address = models.TextField(verbose_name="آدرس کامل")
    # Legacy free-text neighbourhood. Superseded by the `district` foreign key
    # below; still written on save so existing readers, search filters and the
    # market-metrics grouping keep working unchanged.
    neighborhood = models.CharField(
        max_length=255, blank=True, verbose_name="محله / منطقه (متنی)"
    )

    # --- location (phase 4) -------------------------------------------------
    # Province and city are reachable through `district.city.province`, so only
    # the leaf is stored. Nullable during the transition: existing rows are
    # backfilled by `migrate_districts_to_hierarchy`.
    district = models.ForeignKey(
        "basics.District",
        on_delete=models.PROTECT,
        related_name="properties",
        null=True,
        blank=True,
        verbose_name="محله",
    )
    description = models.TextField(blank=True, verbose_name="توضیحات")

    latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        verbose_name="عرض جغرافیایی",
    )
    longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        verbose_name="طول جغرافیایی",
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.AVAILABLE,
        verbose_name="وضعیت",
    )

    is_shared = models.BooleanField(
        default=False,
        verbose_name="نمایش برای همه مشاوران",
        help_text="وقتی فعال باشد، همه مشاوران ملک را می‌بینند و می‌توانند ویرایش کنند (به جز تغییر مشاور مسئول).",
    )

    # --- owner contact (اطلاعات مالک) --------------------------------------
    # Fields are nullable at the database level so historical rows and the
    # REST API stay compatible during the transition; the write path enforces
    # them (the create serializer and the front-end form require them).
    owner_first_name = models.CharField(
        max_length=100, blank=True, default="", verbose_name="نام مالک"
    )
    owner_last_name = models.CharField(
        max_length=100, blank=True, default="", verbose_name="نام خانوادگی مالک"
    )
    owner_phone = models.CharField(
        max_length=20, blank=True, default="", verbose_name="شماره موبایل مالک"
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاریخ ایجاد")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="تاریخ بروزرسانی")

    objects = models.Manager()
    active_objects = ActivePropertyManager()

    class Meta:
        verbose_name = "ملک"
        verbose_name_plural = "املاک"
        ordering = ["-created_at"]
        indexes = [
            # Every list endpoint orders by newest first, so without this the
            # planner reads the whole table and top-N sorts it on every
            # request — measured at 30,000 rows that is a 30,000-row
            # sequential scan and a heapsort for a page of 100.
            models.Index(fields=["-created_at"], name="idx_property_created_at"),
            # The two filters the list UI sends most often each narrow the set
            # and then apply that same ordering, so they get the ordering
            # column in the index rather than sorting what the filter leaves.
            models.Index(
                fields=["status", "-created_at"], name="idx_property_status_created"
            ),
            models.Index(
                fields=["deal_type", "-created_at"], name="idx_property_deal_created"
            ),
            models.Index(fields=["property_type"], name="idx_property_type"),
            # Range filters. ``price`` is the legacy column the price filter
            # falls back to for records created before pricing moved onto
            # listings; current records resolve through the listing instead.
            models.Index(fields=["area"], name="idx_property_area"),
            models.Index(fields=["price"], name="idx_property_price"),
        ]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        """Mirror the linked district's name into the legacy text column and
        auto-generate the sequential internal_code for new instances.
        """
        # Only a brand-new row without a usable code goes through the sequence.
        # Anything else — an update, or a row that already carries a ZF_ code —
        # keeps the plain save path, so this changes nothing for them.
        needs_code = self.pk is None and not _has_sequential_code(self.internal_code)
        if needs_code:
            self.internal_code = _generate_next_internal_code()

        if self.district_id:
            name = self.district.display_name
            if self.neighborhood != name:
                self.neighborhood = name
                update_fields = kwargs.get("update_fields")
                if update_fields is not None and "neighborhood" not in update_fields:
                    kwargs["update_fields"] = list(update_fields) + ["neighborhood"]

        if not needs_code:
            super().save(*args, **kwargs)
            return

        # The code is derived from the highest one already stored, so two
        # consultants saving at the same moment both read the same maximum and
        # both try to insert it. The unique index rejects the loser; re-reading
        # the maximum then yields the following code. PostgreSQL blocks the
        # duplicate insert until the winner commits, so the retry always sees
        # committed data rather than racing again.
        #
        # ``transaction.atomic()`` is what makes the retry legal: it runs as a
        # savepoint when the caller is already in a transaction, so the failed
        # insert does not poison the surrounding one.
        for attempt in range(CODE_INSERT_ATTEMPTS):
            try:
                with transaction.atomic():
                    super().save(*args, **kwargs)
                return
            except IntegrityError:
                if attempt == CODE_INSERT_ATTEMPTS - 1:
                    raise
                self.internal_code = _generate_next_internal_code()


# --- internal code sequence -------------------------------------------------
#
# ``ZF_`` followed by digits 1-9 only — never a zero — starting at ZF_1111.
# The width grows once a tier runs out, which is the whole point of the
# constants below: 4 digits hold 9**4 = 6561 codes (ZF_1111..ZF_9999), then 5
# digits hold 9**5 = 59049 more (ZF_11111..ZF_99999).
CODE_PREFIX = "ZF_"
FIRST_CODE_VALUE = 1111
#: Inclusive ceiling of the sequence. Widen it to extend the range; the regex,
#: the formatting and the exhaustion check all derive from these constants, so
#: this is the only number that needs changing.
MAX_CODE_VALUE = 99999
#: How many times an insert may lose the race for a code before giving up.
CODE_INSERT_ATTEMPTS = 5

_MIN_WIDTH = len(str(FIRST_CODE_VALUE))  # 4
_MAX_WIDTH = len(str(MAX_CODE_VALUE))    # 5
# Matches every code this module can produce, at *any* width in range. Reading
# only 4-digit codes was the original bug: the first 5-digit code was invisible
# to the next call, which handed out the same code again and every insert after
# ZF_11111 died on the unique index.
_CODE_REGEX = rf"^{CODE_PREFIX}[1-9]{{{_MIN_WIDTH},{_MAX_WIDTH}}}$"


def _has_sequential_code(value):
    """Whether ``value`` already looks like a code this sequence produced."""
    return bool(value) and str(value).startswith(CODE_PREFIX)


def _highest_code_value():
    """Largest numeric suffix in use, or one below the first code.

    Computed in SQL rather than in Python: the previous version pulled every
    matching code into the process and looped over it, which cost 13 ms at
    6561 rows and grew linearly from there. The regex admits digits only, so
    the cast cannot fail.
    """
    highest = (
        Property.objects.filter(internal_code__regex=_CODE_REGEX)
        .annotate(
            code_value=Cast(
                Substr("internal_code", len(CODE_PREFIX) + 1), BigIntegerField()
            )
        )
        .aggregate(highest=Max("code_value"))["highest"]
    )
    return FIRST_CODE_VALUE - 1 if highest is None else highest


def _generate_next_internal_code():
    """Return the next code in the sequence, widening when a tier runs out.

    Sequence rules:

    * starts at ``ZF_1111``;
    * digits 1-9 only — any value containing a zero is skipped, so ``ZF_9999``
      is followed by ``ZF_11111`` rather than ``ZF_10000``;
    * globally unique, and derived from every code in the table regardless of
      width, so the switch from 4 digits to 5 is seamless: the property created
      after the last 4-digit code is the first one registered with a 5-digit
      code.
    """
    value = _highest_code_value() + 1
    while "0" in str(value) and value <= MAX_CODE_VALUE:
        value += 1

    if value > MAX_CODE_VALUE:
        raise RuntimeError(
            f"فضای کدهای داخلی به پایان رسیده است (آخرین کد ممکن: "
            f"{CODE_PREFIX}{MAX_CODE_VALUE})."
        )

    # ``0{_MIN_WIDTH}d`` is a *minimum* width, so 1111 renders as "1111" and
    # 11111 as "11111" — one expression covers every tier.
    return f"{CODE_PREFIX}{value:0{_MIN_WIDTH}d}"


class PropertyAttributeValue(BaseAttributeValue):
    """A dynamic attribute value for one property.

    Only non-core attributes land here; core ones (متراژ، تعداد اتاق …) live in
    real columns on :class:`Property` so they stay fast to filter on.
    """

    property = models.ForeignKey(
        "properties.Property",
        on_delete=models.CASCADE,
        related_name="attribute_values",
        verbose_name="ملک",
    )

    class Meta:
        db_table = "properties_property_attribute_value"
        verbose_name = "مقدار ویژگی ملک"
        verbose_name_plural = "مقادیر ویژگی ملک"
        constraints = [
            models.UniqueConstraint(
                fields=["property", "attribute"],
                name="uq_property_attribute_value",
            )
        ]
        indexes = [
            # One index per typed column: filtering by a dynamic attribute
            # always narrows on attribute_id first, then the matching value.
            models.Index(fields=["attribute", "value_integer"], name="idx_pav_attr_int"),
            models.Index(fields=["attribute", "value_decimal"], name="idx_pav_attr_dec"),
            models.Index(fields=["attribute", "value_boolean"], name="idx_pav_attr_bool"),
            models.Index(fields=["attribute", "value_date"], name="idx_pav_attr_date"),
        ]


class PropertyImage(models.Model):
    property = models.ForeignKey(
        "properties.Property",
        on_delete=models.CASCADE,
        related_name="images",
        verbose_name="ملک",
    )
    image = models.ImageField(
        upload_to="properties/images/",
        verbose_name="تصویر",
        validators=[validate_property_image],
    )
    sort_order = models.PositiveIntegerField(default=0, verbose_name="ترتیب نمایش")

    class Meta:
        verbose_name = "تصویر ملک"
        verbose_name_plural = "تصاویر ملک"
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"{self.property.title} - Image {self.pk}"


def appraisal_report_upload_path(instance, filename):
    """Storage path for an appraisal PDF.

    The stored name is random and URL-safe (Persian/space-laden original
    names cause needless trouble on filesystems and in URLs); the
    user-facing name is preserved in ``original_filename`` and used for the
    download's Content-Disposition instead.
    """
    ext = os.path.splitext(filename)[1].lower()
    if ext != ".pdf":
        ext = ".pdf"
    return f"properties/appraisals/{instance.property_id}/{uuid.uuid4().hex}{ext}"


class PropertyAppraisalReport(models.Model):
    """The single PDF appraisal report (گزارش کارشناسی) attached to a property.

    One report per property — the OneToOneField enforces that at the
    database level. Uploading again replaces the previous row and its file
    (see ``PropertyViewSet.appraisal_report``), so exactly one PDF exists at
    any time and no orphaned files are left behind.
    """

    property = models.OneToOneField(
        "properties.Property",
        on_delete=models.CASCADE,
        related_name="appraisal_report",
        verbose_name="ملک",
    )
    file = models.FileField(
        upload_to=appraisal_report_upload_path,
        validators=[validate_appraisal_pdf],
        verbose_name="فایل گزارش کارشناسی",
        help_text="فقط فایل PDF، حداکثر ۱۰ مگابایت.",
    )
    # Kept apart from the stored path so downloads keep the name the
    # consultant chose, while storage stays URL-safe.
    original_filename = models.CharField(max_length=255, verbose_name="نام اصلی فایل")
    file_size = models.PositiveIntegerField(verbose_name="حجم فایل (بایت)")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_appraisal_reports",
        verbose_name="بارگذاری‌کننده",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاریخ ایجاد")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="تاریخ بروزرسانی")

    class Meta:
        verbose_name = "گزارش کارشناسی ملک"
        verbose_name_plural = "گزارش‌های کارشناسی ملک"

    def __str__(self):
        return f"{self.property.title} - {self.original_filename}"

    def delete(self, *args, **kwargs):
        # Remove the row first, then the stored PDF: if the database delete
        # fails the file is left untouched (no dangling row), while a failed
        # file delete can at worst leave an orphan on disk. FileSystemStorage
        # treats a missing file as a no-op, so this never raises on re-runs.
        pk = self.pk
        super().delete(*args, **kwargs)
        if pk is not None:
            self.file.delete(save=False)