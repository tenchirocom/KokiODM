# tenchiro/models/pool.py
from django.conf import settings                        # type: ignore
from django.db import models                            # type: ignore

from .defaults import RESOURCE_CLASS_AGGREGATE, RESOURCE_CLASS_CONSUMED, RESOURCE_CLASS_CHOICES, RESOURCE_CLASS_BY_NAME

class UsagePool(models.Model):

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='tenchiro_usage_pools',
    )
    resource_name = models.CharField(max_length=64)   # e.g. disk_bytes, cpu_seconds
    resource_class = models.CharField(
        max_length=32,
        choices=RESOURCE_CLASS_CHOICES,
        default=RESOURCE_CLASS_CONSUMED,
    )
    available = models.DecimalField(max_digits=20, decimal_places=6)
    portal_message_uuid = models.CharField(max_length=64, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'tenchiro_usage_pools'
        unique_together = (('user', 'resource_name'),)
        indexes = [
            models.Index(fields=['user', 'resource_name']),
            models.Index(fields=['user', 'updated_at']),
            models.Index(fields=['portal_message_uuid']),
        ]
        ordering = ['user', 'resource_name']

    def __str__(self):
        return f"{self.user_id} | {self.resource_name}={self.available} ({self.resource_class})"

    @classmethod
    def get_pools(cls, user, resource_names=None):
        """
        Current pool rows for a user.

        Parameters:
        - user: The resource owner.
        - resource_names: (optional) A list of the resource names to get. If
            resource_names is None, include all resources for the user.

        Return current pool rows for a user as a dict keyed by resource name.
            Example:
                {
                    "disk_bytes":  <UsagePool>,
                    "cpu_seconds": <UsagePool>,
                }
        """
        qs = cls.objects.filter(user=user)
        if resource_names is not None:
            qs = qs.filter(resource_name__in=resource_names)
        return {row.resource_name: row for row in qs}

    @classmethod
    def get_available(cls, user, resource_names=None):
        """
        Convenience: { resource_name: Decimal available }
        Missing names are omitted (caller decides default).
        """
        return {
            name: row.available
            for name, row in cls.get_pools(user, resource_names).items()
        }

    @classmethod
    def update_pools(cls, user, available_resources: dict, message_uuid: str = None) -> None:
        """
        Update cached pool availability from the Portal.

        available_resources:
            { "disk_bytes": Decimal|str|int, "cpu_seconds": Decimal|str|int, ... }
            { "disk_bytes": { 'available': Decimal, 'class': 'aggregate' }, "cpu_seconds": Decimal|str|int, ... }

        Class (aggregate vs consumed) is resolved locally via RESOURCE_CLASS_BY_NAME.
        Unknown names default to consumed.
        """
        for resource_name, spec in available_resources.items():
            if isinstance(spec, dict):
                available = spec['available']
                resource_class = spec.get('class', RESOURCE_CLASS_BY_NAME.get(resource_name) or RESOURCE_CLASS_CONSUMED)
            else:
                available = spec
                resource_class = RESOURCE_CLASS_BY_NAME.get(resource_name) or RESOURCE_CLASS_CONSUMED

            cls.objects.update_or_create(
                user=user,
                resource_name=resource_name,
                defaults={
                    'resource_class': resource_class,
                    'available': available,
                    'portal_message_uuid': message_uuid,
                },
            )
   