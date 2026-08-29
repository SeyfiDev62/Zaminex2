"""State-only migration: Notification moved to apps.notifications.

The physical table ``common_notification`` and its rows are untouched —
apps.notifications claimed the model first (notifications.0001_initial).
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("common", "0014_remove_activitylog"),
        ("notifications", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[migrations.DeleteModel(name="Notification")],
            database_operations=[],
        ),
    ]
