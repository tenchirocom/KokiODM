from app.plugins import PluginBase, MountPoint
from django.shortcuts import redirect
from django.contrib.auth import login
from django.contrib.auth.models import User
from django.core.cache import cache
from django.utils.translation import gettext as _
from rest_framework_jwt.settings import api_settings
from django.conf import settings
import time


class Plugin(PluginBase):

    def include_js_files(self):
        '''
        Injects javascript files into the overall application templates.

        Modules:
        - hide_navbar: Function to hide the top navbar when configured as sso.
        '''
        return ['js/hide_navbar.js']

    def app_mount_points(self):
        '''
        Plugin endpoints. Defines the custom urls and endpoint for the plugin.

        URLS:
        - sso/ - This converts a JWT token into an active session
        '''
        # Endpoint definitions
        def jwt_sso(request):
            token = request.GET.get('jwt')

            if not token:
                return redirect('/login/')

            # Single-use token check
            #      **n.b. when DEBUG active, the cache doesn't save anything, so disable
            if not settings.DEBUG:
                cache_key = f"used_sso_token:{token}"
                if cache.get(cache_key):
                    return redirect('/login/?error=token_expired')

            try:
                # Decode and validate the JWT
                jwt_decode = api_settings.JWT_DECODE_HANDLER
                payload = jwt_decode(token)

                # Never allow SSO for superusers
                user = User.objects.get(id=payload['user_id'])
                if user.is_superuser or user.is_staff:
                    return redirect('/login/?error=forbidden')

                # Mark token as used (expire after 5 minutes as safety net)
                if not settings.DEBUG:
                    # Use JWT expiry so token can't be reused within its valid lifetime
                    expiry = payload.get('exp', 0) - int(time.time())
                    timeout = getattr(settings, 'SSO_CACHE_EXPIRY', expiry if expiry > 0 else 300)
                    cache.set(cache_key, True, timeout=timeout)

                # Create the Django session
                login(request, user, backend='django.contrib.auth.backends.ModelBackend')
 
                # Redirect to the dashboard after login
                response = redirect('/dashboard/')
                # Set the cookie to indicate sso session
                format_opts = request.GET.get('format', "none")
                response.set_cookie(
                    'ssox_format',
                    format_opts,
                    httponly=False,
                    path='/',
                    samesite='Lax'
                )
                return response

            except User.DoesNotExist:
                return redirect('/login/?error=user_not_found')
            except Exception:
                return redirect('/login/?error=invalid_token')
            
        # Url definitions
        return [
            MountPoint('login/$', jwt_sso),
        ]
