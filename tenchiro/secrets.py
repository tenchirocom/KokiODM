import os
from pathlib import Path
from .apps import logger
from django.conf import settings                        # type: ignore
from django.utils.crypto import constant_time_compare   # type: ignore
from django.core.cache import cache                     # type: ignore

class Secrets:
    """
    Interface to access secrets securely. Secrets are kept in files in the secrets
    directory. This keeps them out of code or environment variables where they are
    more likely to be exposed.
    """

    def __init__(self):
        # Resolves the directory path from environmental variables or falls back to system defaults.
        self.secrets_dir = Path(os.getenv("TENCHIRO_API_SECRETS_DIR", "/run/secrets"))
        self.secret_file = os.getenv("TENCHIRO_API_SECRET", "tenchiro_api_secret")

    def get(self, filename:str) -> str:
        """
        Reads the content of a specific file inside the initialized secrets directory.
        Returns the stripped text string if found, otherwise returns None.
        """
        # Check cache first
        cache_key = f"tenchiro_secrets_file_cache_{filename if filename else 'default'}"
        v = cache.get(cache_key)

        # Bound check: valid cache value
        if not v:
            # Use self.secrets_dir directly to maintain correct Path object manipulation.
            secret_file = self.secrets_dir / (filename or self.secret_file)
            
            if secret_file.exists():
                # Get the new value
                v = secret_file.read_text().strip()
                # Warm the cache
                cache.set(cache_key, v, getattr(settings, 'SECRETS_CACHE_TTL', 3600))
            else:
                # Log critical configuration error
                logger.critical(f"[tenchiro][secrets] CONFIGUATION ERROR: API secret not set in file={secret_file}.")

        return v
    
    def authenticate(self, request, filename:str=None):
        # Secure verification check for the WebODM plugin token
        auth_header = request.headers.get('X-Internal-API-Key')
        # Authenticate secret. Do not permit null or empty secrets. Use constant time for time comparison attacks.
        if not auth_header or not constant_time_compare(auth_header, self.get(filename if filename is not None else self.secret_file)):
            return False
        
        return True

    def header(self, filename:str=None):
        secret = self.get(filename)
        return { 'X-Internal-API-Key': secret } if secret else {}
                