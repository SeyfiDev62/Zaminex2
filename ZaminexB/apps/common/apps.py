from django.apps import AppConfig


class CommonConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.common'
    verbose_name = "عمومی و سیستم"

    def ready(self):
        import apps.common.signals  # noqa: F401
        # Phase 4: signal-driven invalidation of the cached aggregations.
        # (The module is idempotently self-registering; the import here is
        # the canonical hook so every entry point gets the receivers.)
        import apps.common.cache_invalidation  # noqa: F401
