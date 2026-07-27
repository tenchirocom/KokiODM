from django.conf import settings                        # type: ignore
from django.db import models                            # type: ignore
# Pre-django 3.x compatibility
try:
    JSONField = models.JSONField  # Django 3.0+
except AttributeError:
    from django.contrib.postgres.fields import JSONField  # type: ignore
from django.utils.translation import gettext_lazy as _  # type: ignore

from .defaults import RESOURCE_CLASS_AGGREGATE, RESOURCE_CLASS_CONSUMED, \
                      RESOURCE_CLASS_CHOICES, RESOURCE_CLASS_BY_NAME

class UsageEvent(models.Model):

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='tenchiro_usage_events'
    )
    usage_type = models.CharField(max_length=64)
    usage_value = models.DecimalField(max_digits=20, decimal_places=6)
    usage_class = models.CharField(
        max_length=32,
        choices=RESOURCE_CLASS_CHOICES,
        default=RESOURCE_CLASS_CONSUMED
    )
    task_uuid = models.CharField(max_length=64, null=True, blank=True, db_index=True)
    metadata = JSONField(default=dict, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'tenchiro_usage_events'
        indexes = [
            models.Index(fields=['user', 'created_at']),
            models.Index(fields=['task_uuid']),
            models.Index(fields=['usage_class', 'created_at']),
            models.Index(fields=['usage_class', 'usage_type']),
            models.Index(
                fields=['user', 'usage_type', 'usage_class', 'created_at'],
                name='idx_usage_usr_typ_cls_creat',
            ),
        ]
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user_id} | {self.usage_type} | {self.usage_value} ({self.usage_class})"