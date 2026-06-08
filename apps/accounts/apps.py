from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    label = "accounts"
    verbose_name = "계정 관리"

    def ready(self) -> None:
        # admin.py 등록이 앱 로드 시 확실히 실행되도록 함
        from . import admin  # noqa: F401