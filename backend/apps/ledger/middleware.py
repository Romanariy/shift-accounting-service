from django.db import connection, transaction


class LedgerMutationMiddleware:
    """Serialize legacy/admin writes with invoice snapshot approval as well."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and "ledger_settings" in connection.introspection.table_names():
            from .engine import lock
            with transaction.atomic():
                lock()
                return self.get_response(request)
        return self.get_response(request)
