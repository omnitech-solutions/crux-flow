"""Minimal Django settings for the ADR-0075 harness fixture.

The harness sets ``DJANGO_SETTINGS_MODULE`` to this module and calls
``django.setup()``. It also injects ``django.db.backends.dummy`` before setup so
any connection attempt fails fast rather than hanging; this file names the dummy
backend directly too, as defense in depth. ``django.setup()`` +
``apps.get_models()._meta`` never touches the database, so nothing here blocks.
"""

SECRET_KEY = "fixture-not-a-secret"  # noqa: S105 — test fixture, not a real key.

INSTALLED_APPS = [
    "runtime_django.blog",
]

ROOT_URLCONF = "runtime_django.urls"

# Dummy backend: setup succeeds, any real query raises ImproperlyConfigured.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.dummy",
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.AutoField"

USE_TZ = True
