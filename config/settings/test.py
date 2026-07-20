"""Test settings — used by pytest and manage.py test in CI."""

import os
import urllib.parse

from .base import *  # noqa: F401,F403

# Use postgres when DATABASE_URL is provided (CI), fall back to sqlite locally.
_database_url = os.environ.get("DATABASE_URL")
if _database_url:
    _url = urllib.parse.urlparse(_database_url)
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": _url.path[1:],  # strip leading /
            "USER": _url.username,
            "PASSWORD": _url.password,
            "HOST": _url.hostname,
            "PORT": _url.port or 5432,
        }
    }
