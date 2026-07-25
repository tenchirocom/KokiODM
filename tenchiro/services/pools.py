from django.db import transaction       # type: ignore
from ..apps import logger
from ..models import UsagePool, WebhookLog, WEBHOOK_SENT, WEBHOOK_RECEIVED

class Pools:
    """
    Pools service class. Instantiated for a user, the class provides methods to get
    and update pools, log webhook messages, and generate sequence numbers required
    for webhook messages.
    """
    def __init__(self, user):
        """
        Constructor. Creates a new Pools object for the specified user.
        """
        self.user = user

    def get_pools(self) -> list:
        """
        Get the user's pools.
        """
        pool_objs = UsagePool.objects.filter(user=self.user)
        return [
            {
                'name': pool_obj.resource_name,
                'class': pool_obj.resource_class,
                'available': pool_obj.available,
                'updated_at': pool_obj.updated_at,
            }
            for pool_obj in pool_objs
        ]

    def update_pools(self, resources_available:dict, message_uuid:str=None):
        """
        Update the respective pools from the resources available dictionary.
        Parameters:
        - resources_available: A dictionary with the list of pool specifications.
        - message_uuid (optional): The message id, if there is one.
        Returns None.
        """
        if not message_uuid:
            message_uuid = WebhookLog.generate_message_uuid()

        with transaction.atomic():
            UsagePool.update_pools(self.user, resources_available, message_uuid)

    def generate_sequence_key(self) -> str:
        """
        Generates the sequence key used with Webhook logs for dedup.
        """
        return WebhookLog.generate_sequence_key(self.user)        

    def log_sent(self, resources_available:dict, message_uuid:str=None, sequence_key:str=None, response_status:int=200):
        """
        Log that a message was sent.
        """
        if message_uuid is None:
            message_uuid = WebhookLog.generate_message_uuid()

        if sequence_key is None:
            sequence_key = self.generate_sequence_key()

        WebhookLog.objects.create(
            service="portal:pools",
            direction=WEBHOOK_SENT,
            message_uuid=message_uuid,
            user=self.user,
            response_status=response_status,
            sequence_key=sequence_key,
            metadata={
                "pool_updates": resources_available,
            }
        )

        return {
            'message_id': message_uuid,
            'sequence_key': sequence_key,
        }

    def log_received(
        self, 
        resources_available:dict, 
        message_uuid:str=None, 
        sequence_key:str=None, 
        response_status:int=200
    ) -> dict:
        """
        Logs an incoming pool message after checking for duplicates and out-of-order sequence keys.

        De-duplication Rules:
        1. Drops if message_uuid already exists in WebhookLog.
        2. Drops if sequence_key <= the latest recorded sequence_key for this user.

        Returns:
        - { 'webhook_log': log_object, 'logged': True } if the message was valid and logged.
        - { 'webhook_log': None, 'logged': False } if the message was dropped as a duplicate or stale.
        """
        if not message_uuid:
            message_uuid = WebhookLog.generate_message_uuid()

        # UUID Duplicate Check
        if WebhookLog.objects.filter(message_uuid=message_uuid).exists():
            logger.warning(f"Duplicate message_uuid skipped: {message_uuid}")
            return { 'webhook_log': None, 'logged': False }

        # Sequence Key Order Check
        if sequence_key:
            latest_log = WebhookLog.objects.filter(
                user=self.user,
                direction=WEBHOOK_RECEIVED,
                sequence_key__isnull=False
            ).order_by('-sequence_key').first()

            if latest_log and latest_log.sequence_key and sequence_key <= latest_log.sequence_key:
                logger.warning(
                    f"Out-of-order sequence key skipped. Incoming: {sequence_key}, Latest: {latest_log.sequence_key}"
                )
                return { 'webhook_log': None, 'logged': False }

        # Create log if valid
        log_entry = WebhookLog.objects.create(
            service="portal:pools",
            direction=WEBHOOK_RECEIVED,
            message_uuid=message_uuid,
            user=self.user,
            response_status=response_status,
            sequence_key=sequence_key,
            metadata={
                "pool_updates": resources_available,
            }
        )

        return { 'webhook_log': log_entry, 'logged': True }

