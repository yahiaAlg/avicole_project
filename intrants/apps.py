from django.apps import AppConfig


class IntrantsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "intrants"
    verbose_name = "Intrants & Fournisseurs"

    def ready(self):
        import intrants.signals  # noqa: F401
