"""State-only migration: AIInsightCache moved to apps.analytics.

The physical table ``common_aiinsightcache`` and its rows are untouched —
apps.analytics claimed the model first (analytics.0001_initial).
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("common", "0012_ticket_choices"),
        ("analytics", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[migrations.DeleteModel(name="AIInsightCache")],
            database_operations=[],
        ),
    ]
