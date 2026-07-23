# tenchiro/urls.py
from django.urls import path
from . import views

app_name = 'tenchiro'

urlpatterns = [
     # i.e. path('webhook/<str:webhook_slug>/', views.WebhookView.as_view(), name='webhook'),
]

