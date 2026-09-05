"""Turn the two hard-coded attribute groups into administrator-managed rows.

Before this migration «دسته‌بندی ویژگی‌ها» offered exactly two groups, baked
into ``Attribute.Category`` and enforced by the field's ``choices``. Adding a
third grouping meant editing code and redeploying, so the groups become rows of
the new :class:`~apps.basics.models.AttributeCategory` table that an
administrator maintains from that same screen.

Three steps, in this order:

  (1) ``CreateModel`` — the new table;
  (2) ``AlterField`` — drop ``choices`` from ``Attribute.category`` (a static
      list would reject every category created after this file was written),
      widen it to 100 characters (system keys are slugified Persian labels) and
      index it, since the category screen groups attributes by this column;
  (3) ``RunPython`` — seed the two existing groups as rows, so every attribute
      already stored under ``essential`` / ``non_essential`` keeps resolving to
      a real category and no data has to move.

The seed is idempotent: it looks the key up before inserting, so re-running the
migration (or running it against a database where the rows were restored from a
backup) cannot duplicate them. The historical model's default manager is a plain
``Manager`` — custom managers are only serialised into migrations with
``use_in_migrations`` — so the lookup also sees soft-deleted rows and a
previously deleted ``essential`` is revived rather than re-created under a new
key.
"""

from decimal import Decimal

from django.db import migrations, models

from apps.basics.categorization import ESSENTIAL, NON_ESSENTIAL

# The two built-in groups, with the labels ``Attribute.Category`` has always
# shown. Spelled out here rather than read from the live model, because a
# migration must keep producing the same result as the model evolves.
BUILTIN_CATEGORIES = (
    (ESSENTIAL, "ویژگی ضروری", Decimal("1")),
    (NON_ESSENTIAL, "ویژگی غیر ضروری", Decimal("2")),
)


def seed_builtin_categories(apps, schema_editor):
    AttributeCategory = apps.get_model("basics", "AttributeCategory")

    for name, display_name, sort_order in BUILTIN_CATEGORIES:
        category, created = AttributeCategory.objects.get_or_create(
            name=name,
            defaults={
                "display_name": display_name,
                "sort_order": sort_order,
                "is_active": True,
            },
        )
        # A row restored from a backup may have been soft-deleted or renamed.
        # The key is what ``Attribute.category`` points at, so the row has to
        # exist and be usable; the label is refreshed to the canonical one so
        # the screen always shows the same two headings.
        if not created and (
            category.deleted_at is not None
            or not category.is_active
            or category.display_name != display_name
        ):
            category.deleted_at = None
            category.is_active = True
            category.display_name = display_name
            category.sort_order = sort_order
            category.save(
                update_fields=[
                    "deleted_at",
                    "is_active",
                    "display_name",
                    "sort_order",
                    "updated_at",
                ]
            )


def remove_builtin_categories(apps, schema_editor):
    AttributeCategory = apps.get_model("basics", "AttributeCategory")
    AttributeCategory.objects.filter(
        name__in=[name for name, _, _ in BUILTIN_CATEGORIES]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("basics", "0003_attribute_category"),
    ]

    operations = [
        migrations.CreateModel(
            name="AttributeCategory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="تاریخ ایجاد")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="تاریخ بروزرسانی")),
                ("is_active", models.BooleanField(db_index=True, default=True, verbose_name="فعال")),
                ("deleted_at", models.DateTimeField(blank=True, db_index=True, null=True, verbose_name="تاریخ حذف")),
                ("name", models.CharField(db_index=True, help_text="شناسه انگلیسی و ثابت (مانند apartment). پس از ایجاد تغییر نکند.", max_length=100, verbose_name="کلید سیستمی")),
                ("display_name", models.CharField(max_length=255, verbose_name="نام نمایشی")),
                ("sort_order", models.DecimalField(decimal_places=2, default=0, max_digits=10, verbose_name="ترتیب نمایش")),
                ("meta_data", models.JSONField(blank=True, default=dict, verbose_name="متادیتا")),
            ],
            options={
                "verbose_name": "دسته\u200cبندی ویژگی",
                "verbose_name_plural": "دسته\u200cبندی\u200cهای ویژگی",
                "db_table": "basics_attribute_category",
                "ordering": ["sort_order", "display_name"],
                "abstract": False,
                "constraints": [models.UniqueConstraint(condition=models.Q(("deleted_at__isnull", True)), fields=("name",), name="uq_attribute_category_name_alive")],
            },
        ),
        migrations.AlterField(
            model_name="attribute",
            name="category",
            field=models.CharField(db_index=True, default="non_essential", help_text="دسته\u200cبندی\u200cای که این ویژگی در آن قرار می\u200cگیرد؛ فهرست دسته\u200cبندی\u200cها از تب «دسته\u200cبندی ویژگی\u200cها» مدیریت می\u200cشود. یک ویژگی همیشه دقیقاً در یک دسته\u200cبندی قرار دارد.", max_length=100, verbose_name="دسته\u200cبندی ویژگی"),
        ),
        migrations.RunPython(seed_builtin_categories, remove_builtin_categories),
    ]
