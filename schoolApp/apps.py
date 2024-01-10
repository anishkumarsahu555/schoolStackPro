from django.apps import AppConfig


class SchoolappConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'schoolApp'

    def ready(self):
        import schoolApp.signals
