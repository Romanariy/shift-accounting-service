from django.contrib import admin
from .models import Service, Rate, Accrual, Record, Settings, TelegramContact, OrganizationBilling, EmployeePreference

for model in (Service, Rate, Accrual, Record, Settings, TelegramContact, OrganizationBilling, EmployeePreference):
    admin.site.register(model)
