from collections.abc import Iterator

import pytest
from django.core.cache import cache
from django.test.utils import override_settings

from config.celery import app as celery_app

PLAIN_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

LOCAL_MEMORY_CACHE = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "tests"}
}

celery_app.finalize()
celery_app.conf.task_always_eager = True


@pytest.fixture(autouse=True)
def isolated_settings() -> Iterator[None]:
    with override_settings(
        STORAGES=PLAIN_STORAGES,
        CACHES=LOCAL_MEMORY_CACHE,
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    ):
        cache.clear()
        yield
        cache.clear()
