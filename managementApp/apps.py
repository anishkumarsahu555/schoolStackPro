from django.apps import AppConfig


class SchoolappConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'managementApp'

    def ready(self):
        import managementApp.signals
