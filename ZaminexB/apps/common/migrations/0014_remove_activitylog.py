"""State-only migration: ActivityLog moved to apps.activity.

The physical table ``common_activitylog`` and its rows are untouched —
apps.activity claimed the model first (activity.0001_initial).
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("common", "0013_remove_aiinsightcache"),
        ("activity", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[migrations.DeleteModel(name="ActivityLog")],
            database_operations=[],
        ),
    ]
