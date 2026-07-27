from decimal import Decimal
from django.contrib.auth import get_user_model  # type: ignore
from django.core.cache import cache             # type: ignore
from django.db.models import Sum, Q             # type: ignore
from app.models.task import Task
from tenchiro.models import UsageEvent, WebhookLog, ServiceType, RESOURCE_CLASS_AGGREGATE, RESOURCE_CLASS_CONSUMED
from tenchiro.services.disk import Disk
from ..apps import logger
from ..models import WEBHOOK_SENT

User = get_user_model()

def record_usage_event(*, user, usage_type, usage_value, usage_class, task_uuid=None, metadata=None):
    """
    Single write path for all usage events.
    Call this from end-of-run handlers today,
    and from a future intermediate updater later.
    """
    UsageEvent.objects.create(
        user=user,
        usage_type=usage_type,
        usage_value=Decimal(usage_value),
        usage_class=usage_class,
        task_uuid=str(task_uuid) if task_uuid else None,
        metadata=metadata or {},
    )

def record_task_completed(task_id: str) -> None:
    logger.info(f"[tenchiro] Task completed.")
    task = Task.objects.select_related('project__owner').get(id=task_id)
    user = task.project.owner

    # CPU: this run only (cumulative)
    try:
        cpu_seconds = task.processing_time / 1000.0 or 0.0
        if cpu_seconds:
            record_usage_event(
                user=user,
                usage_type='cpu_seconds',
                usage_value=Decimal(str(cpu_seconds)),
                usage_class=RESOURCE_CLASS_CONSUMED,
                task_uuid=task.id,
            )
    except Exception as e:
        logger.exception(f"[tenchiro][usage][record task completed] Exception encountered recording CPU usage: {str(e)}")

    # Disk: point-in-time aggregate of all current tasks
    try:
        total_disk = Disk.task_usage(Task.objects.filter(project__owner=user))

        record_usage_event(
            user=user,
            usage_type='disk_bytes',
            usage_value=total_disk,
            usage_class=RESOURCE_CLASS_AGGREGATE,
            task_uuid=task.id,
        )
    except Exception as e:
        logger.exception(f"[tenchiro][usage][record task completed] Exception encountered recording DISK usage: {str(e)}")

def record_task_failed(task_id: str) -> None:
    logger.info(f"[tenchiro] Task failed.")
    # Same idea — record whatever CPU was consumed before failure
    task = Task.objects.select_related('project__owner').get(id=task_id)
    user = task.project.owner

    try:
        cpu_seconds = cpu_seconds = task.processing_time / 1000.0 or 0.0
        if cpu_seconds:
            record_usage_event(
                user=user,
                usage_type='cpu_seconds',
                usage_value=Decimal(str(cpu_seconds)),
                usage_class=RESOURCE_CLASS_CONSUMED,
                task_uuid=task.id,
            )
    except Exception as e:
        logger.exception(f"[tenchiro][usage][record task failed] Exception encountered recording CPU usage: {str(e)}")

def _task_user_key(task_id: str) -> str:
    return f"tenchiro:task_user:{task_id}"

def record_task_removing(task_id: str) -> None:
    """Called from task_removing - just remember who owns the task."""
    logger.info(f"[tenchiro] Removing task id={task_id}.")
    task = Task.objects.select_related('project__owner').get(id=task_id)
    cache.set(_task_user_key(task_id), task.project.owner_id, timeout=300)

def record_task_removed(task_id: str) -> None:
    """Called from task_removed - task is gone, now write the correct aggregate."""
    logger.info(f"[tenchiro] Removed task id={task_id}.")
    key = _task_user_key(task_id)
    user_id = cache.get(key)
    if user_id is None:
        return
    cache.delete(key)

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        logger.warning(f"[tenchiro][usage][record task removed] User not available for deleted task id={task_id}: {str(e)}")
        return

    # Disk: point-in-time aggregate of all current tasks
    try:
        total_disk = sum(
            Disk.directory_size(t.get_data_path())
            for t in Task.objects.filter(project__owner=user)
        )

        record_usage_event(
            user=user,
            usage_type='disk_bytes',
            usage_value=Decimal(str(total_disk)),
            usage_class=RESOURCE_CLASS_AGGREGATE,
            task_uuid=task_id,
        )
    except Exception as e:
        logger.exception(f"[tenchiro][usage][record task removed] Exception encountered recording DISK usage: {str(e)}")

class Usage:

    def __init__(self, user):
        self.user = user

    def get_disk_events(self) -> list[UsageEvent]:
        """Returns a list with the latest aggregate disk UsageEvent (or empty list)."""
        latest = (
            UsageEvent.objects
            .filter(user=self.user, usage_type='disk_bytes', usage_class=RESOURCE_CLASS_AGGREGATE)
            .order_by('-created_at')
            .first()
        )
        return [latest] if latest else []

    def get_cpu_events(self, since=None) -> list[UsageEvent]:
        """Returns a evaluated list of unacknowledged (or since date) CPU UsageEvents."""
        qs = UsageEvent.objects.filter(
            user=self.user,
            usage_type='cpu_seconds',
            usage_class=RESOURCE_CLASS_CONSUMED,
        )
        if since:
            qs = qs.filter(created_at__gte=since)
        else:
            qs = qs.exclude(
                webhook_logs__direction=WEBHOOK_SENT,
                webhook_logs__response_status=200,
            )

        # Force immediate evaluation to list to snapshot records and avoid duplicate DB hits
        return list(qs)

    def log_sent(self, all_usage:dict, message_uuid:str=None, sequence_key:str=None, response_status:int=200, additional_meta:dict=None) -> dict:
        """
        Logs an outbound webhook transmission attempt and attaches all unsent UsageEvent 
        records for this user to track delivery state.
        """
        # Fallback generation for message tracking identifiers: uuid and sequence key
        if not message_uuid:
            message_uuid = WebhookLog.generate_message_uuid()

        if not sequence_key:
            sequence_key = WebhookLog.generate_sequence_key(self.user)

        # Collect all events from all sections cleanly
        events_to_link = []
        for key in ('usage', 'consume', 'reserve'):
            events_to_link.extend(all_usage.get(key) or [])

        # Calculate exact Decimal sums
        disk_total = sum((e.usage_value for e in all_usage.get('usage', []) if e.usage_type == 'disk_bytes'), Decimal('0'))
        cpu_total = sum((e.usage_value for e in all_usage.get('consume', []) if e.usage_type == 'cpu_seconds'), Decimal('0'))

        # Build JSON metadata payload from the list of events
        json_metadata_payload = {
            'usage': {
                'disk_bytes': disk_total
            },
            'consume': {
                'cpu_seconds': cpu_total
            },
            'reserve': None,
        }

        # Create WebhookLog record
        webhook_log = WebhookLog.objects.create(
            user=self.user,
            service=ServiceType.PORTAL_USAGE,
            message_uuid=message_uuid,
            sequence_key=sequence_key,
            direction=WEBHOOK_SENT,
            response_status=response_status,
            metadata={
                **additional_meta,
                **json_metadata_payload,
            },
        )

        # Wire up M2M relationship in bulk
        if events_to_link:
            webhook_log.usage_events.add(*events_to_link)

        logger.info(
            f"[tenchiro][usage] Logged WEBHOOK_SENT | user={self.user.username} | "
            f"status={response_status} | events_linked={len(events_to_link)} | "
            f"msg_uuid={message_uuid}"
        )

        return {
            **additional_meta,
            **json_metadata_payload,
            'webhook_log': webhook_log,
            'logged': True,
            'message_uuid': message_uuid,
            'sequence_key': sequence_key,
        }

