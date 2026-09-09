from collections.abc import Iterator

import pytest
from django.test.utils import override_settings

PLAIN_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@pytest.fixture(autouse=True)
def plain_static_storage() -> Iterator[None]:
    with override_settings(STORAGES=PLAIN_STORAGES):
        yield
