import os
from decimal import Decimal
from django.contrib.auth import get_user_model  # type: ignore
from django.core.cache import cache             # type: ignore
from django.db.models import Sum                # type: ignore
from app.models.task import Task
from tenchiro.models import UsageEvent, RESOURCE_CLASS_AGGREGATE, RESOURCE_CLASS_CONSUMED
from tenchiro.services.disk import Disk
from ..apps import logger

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


# Current disk (latest aggregate, or recompute live)
def get_user_disk_usage(user) -> Decimal:
    latest = (
        UsageEvent.objects
        .filter(user=user, usage_type='disk_bytes', usage_class=RESOURCE_CLASS_AGGREGATE)
        .order_by('-created_at')
        .first()
    )
    return latest.usage_value if latest else Decimal(0)

# CPU consumed in a period (or lifetime)
def get_user_cpu_usage(user, since=None) -> Decimal:
    qs = UsageEvent.objects.filter(
        user=user,
        usage_type='cpu_seconds',
        usage_class=RESOURCE_CLASS_CONSUMED,
    )
    if since:
        qs = qs.filter(created_at__gte=since)
    return qs.aggregate(total=Sum('usage_value'))['total'] or Decimal(0)