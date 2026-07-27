# tenchiro/admin.py
from django.contrib import admin            # type: ignore
from django.utils.html import format_html   # type: ignore
from .models import UsageEvent, Setting, WebhookLog, WEBHOOK_SENT, WEBHOOK_RECEIVED

@admin.register(Setting)
class SettingAdmin(admin.ModelAdmin):
    """
    Admin configuration for the app-scoped singleton Setting model.
    Enforces a strict single-instance management experience (hiding 'Add' once created).
    """
    list_display = (
        '__str__',
        'webhook_applabel',
        'webhook_appname',
        'webhook_enabled',
        'webhook_target_url',
        'webhook_batch_size',
    )
    
    fieldsets = (
        ('General Options', {
            'fields': (
                'webhook_applabel',
            ),
            'description': 'Configure general app parameters.'
        }),
        ('Webhook Dispatcher', {
            'fields': (
                'webhook_appname',
                'webhook_enabled',
                'webhook_target_url',
                'webhook_batch_size',
                'webhook_max_retries',
                'webhook_timeout_seconds',
            ),
            'description': 'Configure outbound synchronization parameters for the portal telemetry service.'
        }),
        ('Advanced / Extensions', {
            'fields': ('metadata',),
            'classes': ('collapse',),
        }),
    )

    def has_add_permission(self, request):
        """
        Prevent creating additional Setting records if one already exists,
        preserving the singleton pattern directly in the Django Admin UI.
        """
        if Setting.objects.exists():
            return False
        return super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        """
        Prevent accidental deletion of the core singleton configuration record.
        """
        return False

@admin.register(UsageEvent)
class UsageEventsAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'user',
        'usage_type',
        'usage_value',
        'usage_class',
        'status_badge',  # Quick visual indicator for errors
        'task_uuid',
        'created_at',
    )
    list_filter = (
        'usage_type',
        'usage_class',
        'created_at',
    )
    search_fields = (
        'user__username',
        'task_uuid',
        'usage_type',
    )
    readonly_fields = (
        'user',
        'usage_type',
        'usage_value',
        'usage_class',
        'task_uuid',
        'created_at',
        'metadata',  # Exposed for viewing webhook errors & payloads
    )
    ordering = ('-created_at',)
    list_per_page = 20
    list_max_show_all = 100
    date_hierarchy = 'created_at'

    def status_badge(self, obj):
        """
        Parses metadata to render a quick status pill for transmission debugging.
        """
        meta = obj.metadata or {}
        
        if meta.get('status') == 'failed' or 'error' in meta:
            error_msg = meta.get('error', 'Transmission Failed')
            return format_html(
                '<span style="color: #b91c1c; background: #fee2e2; padding: 3px 8px; '
                'border-radius: 12px; font-weight: bold; font-size: 11px;" title="{}">Error</span>',
                error_msg
            )
        elif meta.get('status') == 'synced' or meta.get('synced_at'):
            return format_html(
                '<span style="color: #15803d; background: #dcfce7; padding: 3px 8px; '
                'border-radius: 12px; font-weight: bold; font-size: 11px;">Synced</span>'
            )
        
        return format_html(
            '<span style="color: #4b5563; background: #f3f4f6; padding: 3px 8px; '
            'border-radius: 12px; font-weight: bold; font-size: 11px;">Pending</span>'
        )
    status_badge.short_description = "Sync Status"

@admin.register(WebhookLog)
class WebhookLogAdmin(admin.ModelAdmin):
    """
    HTTP Transaction audit log for inbound/outbound webhook traffic.
    Readonly configuration for debugging message transmissions and batch events.
    """
    list_display = (
        'id',
        'direction_badge',
        'service',
        'status_badge',
        'message_uuid',
        'sequence_key',
        'user',
        'created_at',
    )
    list_filter = (
        'direction',
        'service',
        'response_status',
        'created_at',
    )
    search_fields = (
        'message_uuid',
        'sequence_key',
        'service',
        'user__username',
    )
    readonly_fields = (
        'user',
        'service',
        'message_uuid',
        'direction',
        'response_status',
        'sequence_key',
        'usage_events',
        'metadata',
        'created_at',
    )
    filter_horizontal = ('usage_events',)
    ordering = ('-created_at',)
    list_per_page = 50
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return True

    def direction_badge(self, obj):
        if obj.direction == WEBHOOK_SENT:
            return format_html('<b style="color: #2563eb;">SENT</b>')
        elif obj.direction == WEBHOOK_RECEIVED:
            return format_html('<b style="color: #7c3aed;">RECEIVED</b>')
        return obj.direction
    direction_badge.short_description = "Direction"

    def status_badge(self, obj):
        if obj.response_status is None:
            return format_html(
                '<span style="color: #dc2626; background: #fee2e2; padding: 2px 6px; '
                'border-radius: 4px; font-weight: bold; font-size: 11px;">NO RESPONSE</span>'
            )
        if 200 <= obj.response_status < 300:
            return format_html(
                '<span style="color: #16a34a; background: #dcfce7; padding: 2px 6px; '
                'border-radius: 4px; font-weight: bold; font-size: 11px;">{} OK</span>',
                obj.response_status
            )
        return format_html(
            '<span style="color: #dc2626; background: #fee2e2; padding: 2px 6px; '
            'border-radius: 4px; font-weight: bold; font-size: 11px;">{} ERR</span>',
            obj.response_status
        )
    status_badge.short_description = "HTTP Status"