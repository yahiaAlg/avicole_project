from django.apps import AppConfig


class AchatsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "achats"

    def ready(self):
        import achats.signals  # noqa: F401
