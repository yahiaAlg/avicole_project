from django.apps import AppConfig


class IntrantsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "intrants"

    def ready(self):
        import intrants.signals  # noqa: F401
