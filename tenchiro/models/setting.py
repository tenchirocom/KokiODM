# tenchiro/models/config.py  (or setting.py)
from django.core.exceptions import ValidationError      # type: ignore
from django.db import models                            # type: ignore
from django.db.models import signals                    # type: ignore
from django.dispatch import receiver                    # type: ignore
from django.utils.translation import gettext_lazy as _  # type: ignore
# Pre-django 3.x compatibility
try:
    JSONField = models.JSONField  # Django 3.0+
except AttributeError:
    from django.contrib.postgres.fields import JSONField  # type: ignore

class Setting(models.Model):
    """
    App-scoped singleton configuration model for Tenchiro SaaS extensions.
    """
    # Webhook Dispatcher
    webhook_appname = models.SlugField(
        max_length=64,
        default="tenchiro-odm",
        help_text=_("The slug name used to uniquely identify the app with the portal service. "
                    "Do not use special characters or spaces other than '_' or '-' in this name. "
                    "The name must be defined in the application configuration in the portal service."
                    )
    )
    webhook_applabel = models.CharField(
        max_length=64,
        default=_("Tenchiro ODM"),
        help_text=_("The user readable label used to describe this app.")
    )
    webhook_enabled = models.BooleanField(
        default=True,
        help_text=_("Toggle outbound webhook dispatching for usage events. "
                    "This synchronizes usage betweeen the app and portal service "
                    "and updates the usage pools."
                    )
    )
    webhook_target_url = models.CharField(
        max_length=512,
        default="https://localhost:8000/pool/api/usage/{appname}/{username}/",
        help_text=_("Destination URL where UsageEvent batches are posted. "
                    "Can incorporate {appname} or {username} if necessary. "
                    "These will be substituted with the corresponding values for the request."
                    )
    )
    webhook_batch_size = models.PositiveIntegerField(
        default=50,
        help_text=_("Maximum number of usage events to bundle per transmission request.")
    )
    webhook_max_retries = models.PositiveIntegerField(
        default=3,
        help_text=_("Maximum retry attempts before flagging a transmission attempt as failed.")
    )
    webhook_timeout_seconds = models.PositiveIntegerField(
        default=10,
        help_text=_("HTTP POST request timeout in seconds.")
    )

    # Future Extension Bucket
    metadata = JSONField(
        default=dict,
        blank=True,
        null=True,
        help_text=_("Auxiliary settings for future SaaS modules without requiring schema migrations.")
    )

    class Meta:
        db_table = 'tenchiro_setting'
        verbose_name = _("Settings")
        verbose_name_plural = _("Settings")

    def __str__(self):
        return f"Tenchiro SX [Webhook={'enabled' if self.webhook_enabled else 'disabled'}]"

    @classmethod
    def get_solo(cls):
        """
        Retrieves or creates the single configuration instance (PK=1).
        """
        obj, _ = cls.objects.get_or_create(id=1)
        return obj
    
    def get_target_url(self, username: str = "") -> str:
        """
        Formats webhook_target_url using the configured webhook_appname 
        and the request's username.
        """
        if not self.webhook_target_url:
            return ""
        try:
            return self.webhook_target_url.format(
                appname=self.webhook_appname,
                username=username or ""
            )
        except (KeyError, ValueError, IndexError):
            # Fallback to unformatted template string if an invalid key was typed in Admin
            return self.webhook_target_url

@receiver(signals.pre_save, sender=Setting, dispatch_uid="tenchiro_setting_pre_save")
def tenchiro_setting_pre_save(sender, instance, **kwargs):
    """
    Enforces WebODM-style single-instance constraint.
    """
    existing = Setting.objects.first()
    if existing and instance.pk != existing.pk:
        raise ValidationError(f"Can only create 1 {Setting.__name__} instance")
    