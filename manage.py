#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys
from pathlib import Path


def _load_env_early() -> None:
    """settings import 전에 .env 를 먼저 로드 (python-dotenv)."""
    env_file = Path(__file__).resolve().parent / ".env"
    try:
        from dotenv import load_dotenv

        load_dotenv(env_file, override=True, encoding="utf-8")
    except ImportError:
        pass


def main():
    _load_env_early()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "kscu_counseling.settings.development")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
