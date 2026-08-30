from django.apps import AppConfig


class ActivityConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.activity'
    verbose_name = "لاگ فعالیت"

    def ready(self):
        # The activity-log receivers are declared with @receiver decorators
        # (string senders, no dispatch_uid) and register at import time.
        # Importing the module here is the single canonical hook — the old
        # import in apps.common.apps.CommonConfig.ready() has been removed.
        import apps.activity.signals  # noqa: F401
