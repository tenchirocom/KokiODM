import os, shutil, uuid
from typing import Union, Iterable
from zoneinfo import ZoneInfo
# Django imports
from django.conf import settings                        # type: ignore
from django.contrib.auth.models import User             # type: ignore
from django.core.cache import cache                     # type: ignore
from django.db import connections                       # type: ignore
from django.db.models import Sum                        # type: ignore
from django.http import JsonResponse                    # type: ignore
from django.views import View                           # type: ignore
from django.utils.decorators import method_decorator    # type: ignore
from django.views.decorators.csrf import csrf_exempt    # type: ignore
from django.utils import timezone                       # type: ignore
from django.utils.translation import gettext as _       # type: ignore
# Webodm imports
from app.models import Project, Task
from nodeodm.models import ProcessingNode
# Local imports
from .apps import logger
from .secrets import Secrets
from .models import Setting
from .services.pools import Pools

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
        cache.set(db_key, metadata['db'], settings.HEALTH_CACHE_TTL)
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
        cache.set(redis_key, metadata['redis'], settings.HEALTH_CACHE_TTL)
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
                cache.set(disk_key, disk_meta, settings.HEALTH_CACHE_TTL)
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

        cache.set(disk_key, disk_meta, settings.HEALTH_CACHE_TTL)
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
                cache.set(nodes_key, nodes_meta, settings.HEALTH_CACHE_TTL)
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

        cache.set(nodes_key, nodes_meta, settings.HEALTH_CACHE_TTL)
        metadata.update(nodes_meta)
        return metadata['nodes']

@method_decorator(csrf_exempt, name='dispatch')
class UserUsageView(View):
    """
    API Endpoint to aggregate computational and storage usage for a given tenant.
    Authenticated via a shared machine-to-machine secret key.
    """
    def get(self, request, *args, **kwargs):
        # Authenticate
        secrets = Secrets()
        if not secrets.authenticate(request):
            return JsonResponse({'status': 'error', 'error': "Invalid or missing API Secret Key."}, status=401)
        
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
                logger.warning(f"[tenchiro][usage] user={user.username}, cluster_id={cluster_id}.")
            else:
                logger.warning(f"[tenchiro][usage] non-existent user ({username}) specified in url, default to full check.")
        else:
            logger.warning(f"[tenchiro][usage] no user specified.")

        # Extract Data Context (Fetch projects and get usage)
        user_projects = Project.objects.filter(owner__username=username)
        usage_totals = get_project_usage(user_projects)
        
        # Fetch the appname from the config dictionary
        config = Setting.get_solo()
        appname = config.webhook_appname
        
        return JsonResponse({
            'status': 'success',    # REQUIRED BY PULL API
            'name': appname,        # REQUIRED BY PULL API
            'label': _("Tenchiro SX"),
            'usage': {              # REQUIRED BY PULL API
                "disk_bytes": usage_totals.get('disk_bytes') or 0,
                "cpu_seconds": usage_totals.get('cpu_seconds') or 0,
            },
            'reserve': None,        # REQUIRED BY PULL API
            'meta': {
                'username': username,
                'total_projects': user_projects.count(),
                'total_tasks': usage_totals.get('total_tasks') or 0
            }
        }, status=200)

@method_decorator(csrf_exempt, name='dispatch')
class UserPoolsView(View):
    """
    API Endpoint to aggregate computational and storage usage for a given tenant.
    Authenticated via a shared machine-to-machine secret key.
    """
    def get(self, request, *args, **kwargs):

        # Authenticate
        secrets = Secrets()
        if not secrets.authenticate(request):
            return JsonResponse({'status': 'fail', 'error': "Invalid or missing API Secret Key."}, status=401)
        
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
                logger.warning(f"[tenchiro][usage] user={user.username}, cluster_id={cluster_id}.")
            else:
                # Returning all pools not supported.
                logger.warning(f"[tenchiro][usage] non-existent user ({username}) specified in url")
                return JsonResponse({
                    'status': 'fail',
                    'error': f"Non-existent user ({username}) specified in url"},
                    status=401
                )
        else:
            # Returning all pools not supported.
            logger.warning(f"[tenchiro][usage] no user specified.")
            return JsonResponse({
                'status': 'fail',
                'error': "No user specified."},
                status=401
            )

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
                'username': username,
            }
        }, status=200)

def get_project_usage(projects:Union[Project, Iterable[Project]]) -> dict:
    # Bound check: singleton project
    if not hasattr(projects, '__iter__'):
        projects = [projects]
        
    user_tasks = Task.objects.filter(project__in=projects)

    logger.warning(f"[tenchiro][usage][get project usage] user_tasks={user_tasks}")

    # Let the Database compute the sum directly
    raw_totals = user_tasks.aggregate(
        total_size=Sum('size'),
        total_time=Sum('processing_time')
    )

    #
    # CPU time is not static. It needs to be time-based
    #

    # 2. Perform your calculations in Python once the data is returned
    disk_bytes = 69 #DiskUsage.megabytes_to_bytes(raw_totals['total_size'] or 0)
    cpu_seconds = int((raw_totals['total_time'] or 0) / 1000)

    return {
        "disk_bytes": disk_bytes,
        "cpu_seconds": cpu_seconds,
        "total_tasks": user_tasks.count()
    }

