"""Public static storage for the QR images intentionally exposed to students."""

import os
from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage


class PublicGroupQRStorage(FileSystemStorage):
    def __init__(self):
        super().__init__(
            location=settings.MEDIA_ROOT,
            base_url=settings.MEDIA_URL,
            file_permissions_mode=0o644,
            directory_permissions_mode=0o755,
        )

    def _save(self, name, content):
        stored_name = super()._save(name, content)
        root = Path(self.location).resolve()
        directory = Path(self.path(stored_name)).resolve().parent
        while directory != root and root in directory.parents:
            os.chmod(directory, 0o755)
            directory = directory.parent
        return stored_name
