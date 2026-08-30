from django.db import models


class AIInsightCache(models.Model):
    """Persisted AI description, keyed by entity so workers cannot mix records.

    LocMemCache is per-process; without a shared store the same property is
    re-sent to the model on every request that hits a different worker, and a
    stale in-memory entry can be served for the wrong record. One row per
    (entity, entity_id) is the source of truth; the in-process cache is only a
    fast path in front of it.
    """

    entity = models.CharField(max_length=20, verbose_name="موجودیت")
    entity_id = models.PositiveIntegerField(verbose_name="شناسه")
    fingerprint = models.CharField(max_length=64, verbose_name="اثر انگشت داده")
    payload = models.JSONField(verbose_name="خروجی مدل")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاریخ ایجاد")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="تاریخ بروزرسانی")

    class Meta:
        # table name pinned during the move; cosmetic rename is a separate future task
        db_table = "common_aiinsightcache"
        verbose_name = "کش تحلیل هوش مصنوعی"
        verbose_name_plural = "کش تحلیل هوش مصنوعی"
        constraints = [
            models.UniqueConstraint(
                fields=["entity", "entity_id"], name="uniq_ai_insight_entity"
            ),
        ]

    def __str__(self):
        return f"{self.entity}:{self.entity_id}"
