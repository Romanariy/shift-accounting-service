import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

    try:
        from django.core.management import execute_from_command_line
    except ImportError as error:
        raise ImportError(
            "Couldn't import Django. Install dependencies with: pip install -r requirements.txt"
        ) from error

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()

