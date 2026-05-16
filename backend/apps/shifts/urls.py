from django.urls import path

from . import views


urlpatterns = [
    path("shifts/employees/", views.employees_api, name="shift-employees"),
    path("shifts/employees/<int:employee_id>/", views.employees_api, name="shift-employee-detail"),
    path("shifts/pay-rules/", views.pay_rules_api, name="shift-pay-rules"),
    path("shifts/pay-rules/<int:rule_id>/", views.pay_rules_api, name="shift-pay-rule-detail"),
    path("shifts/entries/", views.entries_api, name="shift-entries"),
    path("shifts/entries/<str:kind>/<int:entry_id>/", views.entry_detail_api, name="shift-entry-detail"),
    path("shifts/telegram/ingest/", views.telegram_ingest_api, name="shift-telegram-ingest"),
    path("shifts/audit-log/", views.audit_log_api, name="shift-audit-log"),
    path("shifts/month-summary/", views.month_summary_api, name="shift-month-summary"),
    path("shifts/report.xlsx", views.report_xlsx_api, name="shift-report-xlsx"),
    path("shifts/sync-status/", views.sync_status_api, name="shift-sync-status"),
]

