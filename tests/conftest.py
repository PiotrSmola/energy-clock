from collections.abc import Iterator

import pytest
from django.core.cache import cache
from django.test.utils import override_settings

PLAIN_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

LOCAL_MEMORY_CACHE = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "tests"}
}


@pytest.fixture(autouse=True)
def isolated_settings() -> Iterator[None]:
    with override_settings(STORAGES=PLAIN_STORAGES, CACHES=LOCAL_MEMORY_CACHE):
        cache.clear()
        yield
        cache.clear()
