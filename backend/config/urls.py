from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path


def healthcheck(_request):
    return JsonResponse({"status": "ok", "service": "shift-accounting"})


urlpatterns = [
    path("", healthcheck),
    path("admin/", admin.site.urls),
    path("api/", include("apps.shifts.urls")),
]

admin.site.site_header = "Shift Accounting Admin"
admin.site.site_title = "Shift Accounting"
admin.site.index_title = "Учет смен и отчетов"

