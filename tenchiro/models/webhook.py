# tenchiro/models/webhook.py
import uuid
from datetime import datetime, timezone
from django.conf import settings                            # type: ignore
from django.contrib.postgres.fields import JSONField        # type: ignore
from django.core.cache import cache                         # type: ignore
from django.core.serializers.json import DjangoJSONEncoder  # type: ignore
from django.db import models                                # type: ignore
from django.utils.translation import gettext_lazy as _      # type: ignore
from .defaults import WEBHOOK_SENT, WEBHOOK_RECEIVED, WEBHOOK_DIRECTION_CHOICES

class WebhookLog(models.Model):

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='tenchiro_webhook_logs',
        help_text=_("Associated user for the transaction, if applicable.")
    )
    service = models.CharField(
        max_length=64,
        default='portal',
        help_text=_("Target or source service (e.g., 'portal').")
    )
    message_uuid = models.CharField(
        max_length=64,
        db_index=True,
        help_text=_("Unique identifier for this specific message transmission.")
    )
    direction = models.CharField(
        max_length=16,
        choices=WEBHOOK_DIRECTION_CHOICES,
        default=WEBHOOK_SENT,
        help_text=_("Whether the message was sent or received by this app.")
    )
    response_status = models.IntegerField(
        null=True,
        blank=True,
        help_text=_("HTTP response status code (e.g., 200, 400, 500).")
    )
    sequence_key = models.CharField(
        max_length=64,
        db_index=True,
        help_text=_("Monotonic sequence string for ordering in format: 2026-07-24T20:59:00.123456Z:001")
    )
    usage_events = models.ManyToManyField(
        'UsageEvent',
        related_name='webhook_logs',
        blank=True,
        db_table='tenchiro_webhook_usage_events',
        help_text=_("Usage events associated with this specific transmission attempt.")
    )
    metadata = JSONField(
        default=dict,
        blank=True,
        null=True,
        encoder=DjangoJSONEncoder,
        help_text=_("Stores small payloads, cached JSON responses for retries, or diagnostic error messages.")
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text=_("Timestamp when the transaction attempt was recorded.")
    )

    class Meta:
        db_table = 'tenchiro_webhook_log'
        indexes = [
            models.Index(fields=['service', 'sequence_key']),
            models.Index(fields=['message_uuid']),
            models.Index(fields=['direction', 'response_status']),
            models.Index(fields=['created_at']),
        ]
        ordering = ['-created_at']

    def __str__(self):
        status_str = f"HTTP {self.response_status}" if self.response_status else "NO RESPONSE"
        return f"{self.direction.upper()} | {self.service} | Msg: {self.message_uuid} | Seq: {self.sequence_key} | {status_str}"

    @classmethod
    def delivered(cls, usage_event):
        """ Check if the event has been delivered. """
        return usage_event.webhook_logs.filter(response_status=200).exists()

    @classmethod
    def log_entries(cls, usage_event):
        return usage_event.webhook_logs.all().order_by('created_at')

    @classmethod
    def generate_message_uuid(cls) -> str:
        return str(uuid.uuid4())

    @classmethod
    def generate_sequence_key(cls, user) -> str:
        """
        Generates an ISO 8601 Zulu timestamp sequence key with nanosecond precision 
        and a padded 4-digit tie-breaker counter (e.g., '2026-07-25T07:39:42.123456789Z#0000').
        
        Uses Django's cache to track sub-millisecond sequence counts per timestamp.
        """
        # Capture current UTC time with maximum available precision
        now = datetime.now(timezone.utc)
        
        # Python datetime precision is microseconds (%f = 6 digits).
        # Format base string: YYYY-MM-DDTHH:MM:SS.ffffff000Z (appending 000 for 9-digit nanosecond output)
        iso_base = now.strftime('%Y-%m-%dT%H:%M:%S.%f000Z')
        
        # Build a granular cache key scoped to the user and exact timestamp
        user_id = getattr(user, 'id', user)
        cache_key = f"seq_counter:{user_id}:{iso_base}"
        
        # 3. Atomically attempt to set counter = 0 if key doesn't exist
        # cache.add returns True if key was set, False if key already exists.
        # We use timeout=1 second since 500us is below standard cache backend TTL resolution.
        if cache.add(cache_key, 0, timeout=1):
            counter = 0
        else:
            # Key already existed for this exact nanosecond string -> increment counter
            try:
                counter = cache.incr(cache_key)
            except (ValueError, TypeError):
                # Fallback handling in case of key expiration race condition
                cache.set(cache_key, 1, timeout=1)
                counter = 1

        # Format counter as padded 4-digit string
        return f"{iso_base}#{counter:04d}"     