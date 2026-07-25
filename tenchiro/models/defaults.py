# tenchiro/models/defaults.py
from django.utils.translation import gettext_lazy as _  # type: ignore

RESOURCE_CLASS_AGGREGATE = 'aggregate'
RESOURCE_CLASS_CONSUMED  = 'consumed'

RESOURCE_CLASS_CHOICES = (
    (RESOURCE_CLASS_AGGREGATE, _('Aggregate')),
    (RESOURCE_CLASS_CONSUMED,  _('Consumed')),
)

RESOURCE_CLASS_BY_NAME = {
    'disk_bytes': RESOURCE_CLASS_AGGREGATE,
    'cpu_seconds': RESOURCE_CLASS_CONSUMED,
}

WEBHOOK_SENT = 'sent'
WEBHOOK_RECEIVED = 'received'

WEBHOOK_DIRECTION_CHOICES = (
    (WEBHOOK_SENT,      _('Sent')),
    (WEBHOOK_RECEIVED,  _('Received')),
)