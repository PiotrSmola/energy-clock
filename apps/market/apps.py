from django.apps import AppConfig
from django.db.backends.signals import connection_created


class MarketConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.market"
    label = "market"

    def ready(self) -> None:
        from apps.market.sqlite import apply_sqlite_pragmas

        connection_created.connect(apply_sqlite_pragmas, dispatch_uid="market.sqlite.pragmas")
