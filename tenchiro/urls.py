# tenchiro/urls.py
from django.urls import path            # type: ignore
from app.plugins import MountPoint
from . import views

app_name = 'tenchiro'

# Mounted at: /tenchiro/

urlpatterns = [
     # i.e. path('webhook/<str:webhook_slug>/', views.WebhookView.as_view(), name='webhook'),
     path('api/usage/<str:username>/', views.UserUsageView.as_view(), name='tenchiro_usage'),
     path('api/pools/<str:username>/', views.UserPoolsView.as_view(), name='tenchiro_pools'),
     path('api/health/<str:username>/', views.HealthView.as_view(), name='tenchiro_user_health'),
     path('api/health/', views.HealthView.as_view(), name='tenchiro_health'),
]

