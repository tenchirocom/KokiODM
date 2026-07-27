import os, shutil, uuid, json
from typing import Union, Iterable
from zoneinfo import ZoneInfo
# Django imports
from django.conf import settings                            # type: ignore
from django.contrib.auth.models import User                 # type: ignore
from django.core.cache import cache                         # type: ignore
from django.core.serializers.json import DjangoJSONEncoder  # type: ignore
from django.db import connections                           # type: ignore
from django.db.models import Sum                            # type: ignore
from django.http import JsonResponse                        # type: ignore
from django.views import View                               # type: ignore
from django.utils.decorators import method_decorator        # type: ignore
from django.views.decorators.csrf import csrf_exempt        # type: ignore
from django.utils import timezone                           # type: ignore
from django.utils.translation import gettext as _           # type: ignore
# Webodm imports
from app.models import Project, Task
from nodeodm.models import ProcessingNode
# Local imports
from .apps import logger, applabel
from .secrets import Secrets
from .models import Setting, WebhookLog
from .services.pools import Pools
from .services.usage import Usage

class ServiceInfo:
    REQUEST_ID_HEADER = 'X-Request-ID'
    REQUEST_ID_QUERY = 'request_id'

@method_decorator(csrf_exempt, name='dispatch')
class UsageViewXX(View):

    def get(self, request, username, *args, **kwargs):
        #
        #  CHECK: IF USER AUTHENTICATED, SHOULD NOT ALLOW ACCESS TO OTHER USER'S
        #  PROJECTS W?O SEPARATE API AUTHENTICATION TOO!
        #
        if not request.user.is_authenticated:
            # Authenticate
            secrets = Secrets()
            if not secrets.authenticate(request):
                return JsonResponse({"error": "Authentication failed."}, status=401)
        
        projects = Project.objects.filter(
            owner__username=username
        ).values('id', 'owner__username')
        
        return JsonResponse({
            str(p['id']): p['owner__username'] for p in projects
        })
    
@method_decorator(csrf_exempt, name='dispatch')
class HealthView(View):
    """
    API Endpoint to determine runtime status of the app.
    Authenticated via a shared machine-to-machine secret key.
    """
    class Status:
        Healthy = "healthy"
        Degraded = "degraded"
        Unhealthy = "unhealthy"

    def get(self, request, *args, **kwargs):
        """
        HTTP GET handler for returning application health status.

        NOTE: This method optionally accepts the kwarg username to narrow down the response
        for a particular user. Without this arguement, it gets full system health. However,
        some users might not require certain parts of the system, namely certain nodes, and
        therefore, while the system may be degraded overall, it is fully operational for that
        user.
        """
        # Authenticate API
        secrets = Secrets()
        if not secrets.authenticate(request):
            return JsonResponse({"error": "Invalid or missing API Secret Key."}, status=401)
        
        # Fetch the appname from the config dictionary
        config = Setting.get_solo()
        app_name = config.webhook_appname
        
        health_status = self.Status.Healthy
        metadata = {
            "db":      "unknown",
            "redis":   "unknown",
            "disk":    "unknown",
            "nodes":   "unknown"
        }
        
        # Check db connection
        health_status = self._highest_severity(
            health_status,
            self._check_db(metadata)
        )

        # Check redis availability
        health_status = self._highest_severity(
            health_status,
            self._check_redis(metadata)
        )

        # Check disk health and usage
        health_status = self._highest_severity(
            health_status,
            self._check_disk(metadata)
        )

        # Get cluster id for the user
        cluster_id = None
        username = kwargs.get("username")
        if username:
            # The username was parsed from the url and placed in kwargs. Fetch the
            # user's cluster id, if specified, and use this to narrow down the node
            # health response.
            user = User.objects.filter(username=username).first()
            if user:
                # An existing user was provided.
                user_profile = getattr(user, 'profile', None)
                cluster_id = getattr(user_profile, 'cluster_id', None) if user_profile else None
                logger.warning(f"[tenchiro][health] user={user.username}, cluster_id={cluster_id}.")
            else:
                logger.warning(f"[tenchiro][health] non-existent user ({username}) specified in url, default to full check.")
        else:
            logger.warning(f"[tenchiro][health] no user specified.")

        # Check processing node availability
        health_status = self._highest_severity(
            health_status,
            self._check_nodes(metadata, cluster_id=cluster_id)
        )

        tokyo_tz = ZoneInfo(getattr(settings, 'TIME_ZONE', "Asia/Tokyo"))
        now = timezone.localtime(timezone.now(), tokyo_tz)

        return JsonResponse({
                "name": app_name,
                "label": _("Tenchiro SX"),
                "timestamp": now.isoformat(),
                "status": health_status,
                "metadata": metadata
        }, safe=False, status=200)
    
    def _highest_severity(self, current, new):
        # Map statuses to severity levels (higher number = worse health)
        severity = {
            self.Status.Healthy: 0,
            self.Status.Degraded: 1,
            self.Status.Unhealthy: 2
        }
        # Return the status that has the highest severity score
        return max(current, new, key=lambda s: severity.get(s, 0))
        
    def _check_db(self, metadata):
        db_key = "health_db_system"

        # Check cache first
        cached_status = cache.get(db_key)
        if cached_status:
            metadata['db'] = cached_status
            return cached_status
            
        try:
            db_conn = connections['default']
            db_conn.ensure_connection()
            metadata['db'] = self.Status.Healthy
        except Exception as e:
            metadata["db"] = self.Status.Unhealthy
            logger.error(f"[tenchiro][health][db] exception: {str(e)}")

        # Cache the value
        cache.set(db_key, metadata['db'], getattr(settings, 'HEALTH_CACHE_TTL', 60))
        return metadata['db']
    
    def _check_redis(self, metadata):
        redis_key = "health_redis_system"

        # Check cache first
        cached_status = cache.get(redis_key)
        if cached_status:
            metadata['redis'] = cached_status
            return cached_status
            
        # Create a completely unique key per request thread using a UUID and PID
        unique_id = uuid.uuid4().hex
        pid = os.getpid()
        unique_key = f"health_ping_{pid}_{unique_id}"
        
        try:
            # 1. Set a completely isolated key with a short TTL (5 seconds)
            cache.set(unique_key, "pong", 5)
            
            # 2. Retrieve it immediately to confirm write/read sequence works
            if cache.get(unique_key) == "pong":
                metadata['redis'] = self.Status.Healthy
            else:
                metadata['redis'] = self.Status.Degraded
                
            # 3. Explicitly clean up immediately rather than waiting for TTL expiration
            cache.delete(unique_key)
            
        except Exception as e:
            # Capture hard failures (e.g., connection timeouts, connection refused)
            metadata['redis'] = self.Status.Unhealthy
            logger.error(f"[tenchiro][health][redis] exception: {str(e)}")

        # Cache the value
        cache.set(redis_key, metadata['redis'], getattr(settings, 'HEALTH_CACHE_TTL', 60))
        return metadata['redis']

    def _check_disk(self, metadata):
        disk_key = "health_disk_system"

        # Check cache first
        cached_data = cache.get(disk_key)
        if cached_data:
            # Unpack the cached measurements back into the live request metadata dict
            metadata.update(cached_data)
            return metadata['disk']
        
        disk_meta = {}
        
        try:
            # Strict Configuration Guard: No MEDIA_ROOT is a hard error
            storage_root = getattr(settings, 'MEDIA_ROOT', None)
            if not storage_root:
                disk_meta['disk'] = self.Status.Unhealthy
                logger.error("[tenchiro][health][disk] MEDIA_ROOT is not configured.")
                cache.set(disk_key, disk_meta, getattr(settings, 'HEALTH_CACHE_TTL', 60))
                metadata.update(disk_meta)
                return metadata['disk']

            # Pure Metadata Check (No physical file writes)
            # This will raise an OSError if the directory doesn't exist or is completely unreadable
            total, used, free = shutil.disk_usage(str(storage_root))
            
            # Calculate usage metrics
            used_percent = (used / total) * 100
            disk_meta['disk_total_gb'] = round(total / (1024**3), 2)
            disk_meta['disk_used_gb'] = round(used / (1024**3), 2)
            disk_meta['disk_free_gb'] = round(free / (1024**3), 2)
            disk_meta['disk_percent_full'] = round(used_percent, 2)

            # 3. Evaluate Threshold Capacity
            if used_percent >= 95.0:
                disk_meta['disk'] = self.Status.Degraded
                logger.error(f"[tenchiro][health][disk] Disk space critically low: {disk_meta['disk_percent_full']}% used.")
            else:
                disk_meta['disk'] = self.Status.Healthy

        except Exception as e:
            # Catches Read-Only filesystems, missing directories, or OS level storage failures
            disk_meta['disk'] = self.Status.Unhealthy
            logger.critical(f"[tenchiro][health][disk] Storage subsystem unrecoverable: {str(e)}")

        cache.set(disk_key, disk_meta, getattr(settings, 'HEALTH_CACHE_TTL', 60))
        metadata.update(disk_meta)
        return metadata['disk']
    
    def _check_nodes(self, metadata, cluster_id=None):
        nodes_key = f"health_node_cluster_{cluster_id if cluster_id else 'all'}"

        # Check cache first
        cached_data = cache.get(nodes_key)
        if cached_data:
            # Unpack the cached measurements back into the live request metadata dict
            metadata.update(cached_data)
            return metadata['nodes']
        
        nodes_meta = {}
        
        try:
            # Pull nodes directly from the cluster database
            if cluster_id is not None:
                nodes = ProcessingNode.objects.filter(cluster_id=cluster_id)
            else:
                nodes = ProcessingNode.objects.all()
            total_nodes = nodes.count()
            
            nodes_meta['nodes_total'] = total_nodes
            nodes_meta['nodes_online'] = 0

            if total_nodes == 0:
                nodes_meta['nodes'] = self.Status.Degraded
                logger.warning(f"[tenchiro][health][nodes] 0 compute nodes configured for cluster: {cluster_id if cluster_id else 'all'}.")
                cache.set(nodes_key, nodes_meta, getattr(settings, 'HEALTH_CACHE_TTL', 60))
                metadata.update(nodes_meta)
                return metadata['nodes']

            # 2. Track node state
            online_count = 0
            
            for node in nodes:
                try:
                    # If WebODM's optimistic mode is configured, force an updated info network roundtrip.
                    # Wrap this tightly so a network timeout on an orphaned node doesn't kill the overall health check.
                    if getattr(settings, 'NODE_OPTIMISTIC_MODE', False):
                        node.update_node_info()
                except Exception as net_err:
                    logger.warning(f"[tenchiro][health][nodes] Failed updating telemetry for {str(node)}: {str(net_err)}")
                
                # Evaluate using the exact method exposed in the ProcessingNodeSerializer
                if node.is_online():
                    online_count += 1

            nodes_meta['nodes_online'] = online_count

            # 3. Determine overall status based on cluster availability
            if online_count == 0:
                nodes_meta['nodes'] = self.Status.Degraded
                logger.error(f"[tenchiro][health][nodes] Cluster {cluster_id if cluster_id else 'all'} offline: 0 working processing nodes.")
                
            elif online_count < total_nodes:
                nodes_meta['nodes'] = self.Status.Degraded
                logger.warning(f"[tenchiro][health][nodes] Cluster {cluster_id if cluster_id else 'all'} degraded: only {online_count} of {total_nodes} online.")
                
            else:
                nodes_meta['nodes'] = self.Status.Healthy

        except Exception as e:
            nodes_meta['nodes'] = self.Status.Unhealthy
            logger.error(f"[tenchiro][health][nodes] Subsystem exception: {str(e)}")

        cache.set(nodes_key, nodes_meta, getattr(settings, 'HEALTH_CACHE_TTL', 60))
        metadata.update(nodes_meta)
        return metadata['nodes']

@method_decorator(csrf_exempt, name='dispatch')
class UserUsageView(View):
    """
    API Endpoint to aggregate computational and storage usage for a given tenant.
    Authenticated via a shared machine-to-machine secret key.
    """
    def get(self, request, *args, **kwargs):

        user, error_response = _auth_and_get_user(request, kwargs)
        if error_response:
            return error_response

        request_uuid = request.headers.get(ServiceInfo.REQUEST_ID_HEADER) or request.GET.get(ServiceInfo.REQUEST_ID_QUERY)
        if request_uuid:
            existing_log = WebhookLog.objects.filter(user=user, message_uuid=request_uuid).first()
            if existing_log:
                return self._build_response(
                    existing_log.message_uuid,
                    existing_log.sequence_key,
                    existing_log.metadata,
                )

        usage = Usage(user)
        disk_events = usage.get_disk_events()
        cpu_events = usage.get_cpu_events()
        project_count = Project.objects.filter(owner=user).count()
        task_count = Task.objects.filter(project__owner=user).count()

        usage_data = usage.log_sent(
            {
                'usage': disk_events,
                'consume': cpu_events
            },
            message_uuid=request_uuid,
            additional_meta={
                'username': user.username,
                'total_projects': project_count or 0,
                'total_tasks': task_count or 0,
            },
            response_status=200
        )

        return self._build_response(
            usage_data.get('message_uuid'),
            usage_data.get('sequence_key'),
            usage_data
        )

    def _build_response(self, message_uuid, sequence_key, usage_data):
        """Constructs response using saved delta metrics + fresh live metadata."""
        # Fetch the appname from the config dictionary
        config = Setting.get_solo()
        appname = config.webhook_appname

        return JsonResponse({
                'status': 'success',
                'name': appname,
                'label': applabel,
                'message_uuid': message_uuid,
                'sequence_key': sequence_key,
                'usage': usage_data.get('usage'),
                'consume': usage_data.get('consume'),
                'reserve': usage_data.get('reserve'),
                'meta': {
                    'username': usage_data.get('username'),
                    'total_projects': usage_data.get('total_projects'),
                    'total_tasks': usage_data.get('total_tasks')
                }
            },
            encoder=DjangoJSONEncoder,
            status=200
        )

@method_decorator(csrf_exempt, name='dispatch')
class UserPoolsView(View):
    """
    API Endpoint to aggregate computational and storage usage for a given tenant.
    Authenticated via a shared machine-to-machine secret key.
    """
    def get(self, request, *args, **kwargs):

        user, error_response = _auth_and_get_user(request, kwargs)
        if error_response:
            return error_response

        # Fetch the appname from the config dictionary
        config = Setting.get_solo()
        appname = config.webhook_appname
        
        # Get pool data for the user
        pools = Pools(user)
        resources_available = pools.get_pools()
        pools.log_sent(resources_available, response_status=200)
        
        return JsonResponse({
                'status': 'success',    # REQUIRED BY PULL API
                'name': appname,        # REQUIRED BY PULL API
                'label': _("Tenchiro SX"),
                'pools': resources_available,
                'meta': {
                    'username': user.username,
                }
            },
            encoder=DjangoJSONEncoder,
            status=200
        )

    def post(self, request, *args, **kwargs):

        user, error_response = _auth_and_get_user(request, kwargs)
        if error_response:
            return error_response

        # Parse payload
        try:
            payload = json.loads(request.body.decode('utf-8'))
            message_uuid = payload.get('message_uuid')
            sequence_key = payload.get('sequence_key')
            resources_available = payload.get('pools_data')
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JsonResponse({'status': 'fail', 'error': 'Invalid JSON body.'}, status=400)

        config = Setting.get_solo()
        appname = config.webhook_appname

        # Execute pool update
        pools = Pools(user)
        log_status = pools.log_received(resources_available, message_uuid=message_uuid, sequence_key=sequence_key, response_status=200)
        if not log_status.get('logged'):
            return JsonResponse({
                    'status':  'ignored',
                    'name':    appname,
                    'label':   applabel,
                    'message': _("Duplicate message. No update to pools."),
                    'meta': {
                        'username': user.username,
                    }
                },
                status=202
            )
        
        pools.update_pools(resources_available, message_uuid)

        return JsonResponse({
                'status':  'success',
                'name':    appname,
                'label':   applabel,
                'message': _("Pools have been updated by {label}.").format(label=applabel),
                'meta': {
                    'username': user.username,
                }
            },
            encoder=DjangoJSONEncoder,
            status=200
        )

# --------- Helper Functions ---------

def _auth_and_get_user(request, kwargs):
    """Helper to handle secret verification and user resolution."""
    secrets = Secrets()
    if not secrets.authenticate(request):
        return None, JsonResponse({'status': 'fail', 'error': "Invalid or missing API Secret Key."}, status=401)

    username = kwargs.get("username")
    if not username:
        logger.warning("[tenchiro][usage] no user specified.")
        return None, JsonResponse({'status': 'fail', 'error': "No user specified."}, status=401)

    user = User.objects.select_related('profile').filter(username=username).first()
    if not user:
        logger.warning(f"[tenchiro][usage] non-existent user ({username}) specified in url")
        return None, JsonResponse({'status': 'fail', 'error': f"Non-existent user ({username}) specified in url"}, status=401)

    user_profile = getattr(user, 'profile', None)
    cluster_id = getattr(user_profile, 'cluster_id', None) if user_profile else None
    logger.warning(f"[tenchiro][usage] user={user.username}, cluster_id={cluster_id}.")

    return user, None

def get_task_count(projects:Union[Project, Iterable[Project]]) -> dict:

    # Bound check: singleton project
    if not hasattr(projects, '__iter__'):
        projects = [projects]
        
    user_tasks = Task.objects.filter(project__in=projects)
    return user_tasks.count()
