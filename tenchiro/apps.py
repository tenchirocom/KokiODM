import sys, os
from django.apps import AppConfig
import logging

logger = logging.getLogger('app.logger')

class TenchiroConfig(AppConfig):
    name = 'tenchiro'

    def ready(self):
        # Only run in the main Gunicorn master / web process
        if os.environ.get('RUN_MAIN') == 'true':
            return

        # Skip management commands
        if len(sys.argv) > 1 and sys.argv[1] in {
            'migrate', 'makemigrations', 'collectstatic',
            'rebuildplugins', 'translate', 'test', 'shell'
        }:
            return

        # Skip Celery
        if 'celery' in sys.argv[0] or 'worker' in sys.argv or 'beat' in sys.argv:
            return

        logger.info("[tenchiro][app] Initialized.")
