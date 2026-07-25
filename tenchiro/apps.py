import sys, os
from django.apps import AppConfig # type: ignore
import logging

logger = logging.getLogger('app.logger')

class TenchiroConfig(AppConfig):
    name = 'tenchiro'

    def ready(self):
        logger.warning(f"[tenchiro][app] ready() ENTERED {os.environ.get('RUN_MAIN')}")

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
        #if 'celery' in sys.argv[0] or 'worker' in sys.argv or 'beat' in sys.argv:
        #    return

        # Imports the signal handlers
        from tenchiro import signals    # noqa: F401

        logger.info("[tenchiro][app] Initialized.")
