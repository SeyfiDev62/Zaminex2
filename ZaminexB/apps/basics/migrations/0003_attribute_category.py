# Generated manually: adds the `category` classification column and backfills
# it for every existing attribute.
#
# Two steps, per the stage brief:
#   (1) AddField `category` (default NON_ESSENTIAL) so the column exists;
#   (2) RunPython that classifies each existing row with the single pure rule
#       from apps.basics.categorization:
#           essential     ⇔  is_core  OR  has ≥1 active binding
#           non_essential ⇔  otherwise
#
# The `is_core` clause is what keeps core attributes (area/price/…) essential
# even when they have no binding row. A binding-only rule would misclassify
# them.
from django.db import migrations, models

from apps.basics.categorization import classify_attribute


def populate_category(apps, schema_editor):
    Attribute = apps.get_model("basics", "Attribute")
    PropertyTypeAttribute = apps.get_model("basics", "PropertyTypeAttribute")
    DealTypeAttribute = apps.get_model("basics", "DealTypeAttribute")

    # The historical model's default manager is a plain Manager (custom
    # managers are only serialised into migrations with use_in_migrations),
    # so this covers every row, including soft-deleted ones.
    for attr in Attribute.objects.all().iterator():
        active_bindings = (
            PropertyTypeAttribute.objects.filter(
                attribute=attr, is_active=True
            ).count()
            + DealTypeAttribute.objects.filter(
                attribute=attr, is_active=True
            ).count()
        )
        attr.category = classify_attribute(attr.is_core, active_bindings)
        attr.save(update_fields=["category"])


def reset_category(apps, schema_editor):
    # Reversible by resetting every row to the safe default; the classification
    # can be re-derived at any time by re-running `populate_category`.
    Attribute = apps.get_model("basics", "Attribute")
    Attribute.objects.update(category="non_essential")


class Migration(migrations.Migration):

    dependencies = [
        ("basics", "0002_province_city_district_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="attribute",
            name="category",
            field=models.CharField(
                choices=[
                    ("essential", "ویژگی ضروری"),
                    ("non_essential", "ویژگی غیر ضروری"),
                ],
                default="non_essential",
                help_text="ضروری ⇔ فیلد ثابت یا دارای حداقل یک اتصال فعال؛ در غیر این صورت غیر ضروری.",
                max_length=20,
                verbose_name="دسته‌بندی ویژگی",
            ),
        ),
        migrations.RunPython(populate_category, reset_category),
    ]
