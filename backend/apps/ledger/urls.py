from django.urls import path
from .views import api
from apps.offers import views as offers

urlpatterns = [
    path("offers/", offers.offers),
    path("offers/<int:pk>/", offers.offers),
    path("offers/<int:pk>/<str:action>/", offers.offers),
    path("offer-config/", offers.config),
    path("offer-profiles/", offers.profiles),
    path("offer-profiles/<int:organization>/", offers.profiles),
    path("offer-profiles/<int:organization>/<str:action>/", offers.profiles),
    path("offer-images/<int:pk>/", offers.image),
    path("<str:resource>/", api),
    path("<str:resource>/<int:pk>/", api),
    path("<str:resource>/<int:pk>/<str:action>/", api),
]
