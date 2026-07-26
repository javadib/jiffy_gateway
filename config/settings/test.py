"""Test settings — used by pytest and manage.py test."""

from .base import *  # noqa: F401,F403

# Use in-memory SQLite database for tests
# This ensures tests don't depend on filesystem access in CI/CD environments
# and provides a clean database state for each test run
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}
