# tenchiro/signals.py
from django.dispatch import receiver # type: ignore
from app.plugins.signals import (
    task_completed,
    task_failed,
    task_removing,
    task_removed,
)
from tenchiro.services.usage import (
    record_task_completed,
    record_task_failed,
    record_task_removing,
    record_task_removed,
)

@receiver(task_completed)
def on_task_completed(sender, task_id, **kwargs):
    record_task_completed(task_id)

@receiver(task_failed)
def on_task_failed(sender, task_id, **kwargs):
    record_task_failed(task_id)

@receiver(task_removing)
def on_task_removing(sender, task_id, **kwargs):
    record_task_removing(task_id)

@receiver(task_removed)
def on_task_removed(sender, task_id, **kwargs):
    record_task_removed(task_id)