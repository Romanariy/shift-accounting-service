from django.urls import path
from .views import api

urlpatterns = [
    path("<str:resource>/", api),
    path("<str:resource>/<int:pk>/", api),
    path("<str:resource>/<int:pk>/<str:action>/", api),
]
