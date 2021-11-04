from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from packaging.version import Version

if sys.version_info >= (3, 8):
    from importlib.metadata import PathDistribution
else:
    from importlib_metadata import PathDistribution


class Package:
    def __init__(self, dist: PathDistribution, info: dict[str, Any] | None | Exception) -> None:
        self.dist: PathDistribution = dist
        self.info: dict[str, Any] | None = None if isinstance(info, Exception) else info
        self.exc = info if isinstance(info, Exception) else None

    @property
    def last_release_at(self) -> datetime:
        last_release = self.last_release
        if last_release is None:
            return datetime.now(timezone.utc)
        return self.last_release["upload_time_iso_8601"]  # type: ignore # Any instead of datetime

    @property
    def last_release(self) -> dict[str, Any] | None:
        if self.info is None or not self.info["releases"]:
            return None
        for version_str, releases in self.info["releases"].items():
            version = Version(version_str)
            if not version.is_devrelease and not version.is_prerelease:
                return releases[0]  # type: ignore
        return next(iter(self.info["releases"].values()))[0]  # type: ignore

    @property
    def name(self) -> str:
        return self.dist.metadata["Name"]  # type: ignore

    @property
    def version(self) -> str:
        return self.dist.version

    @property
    def path(self) -> Path:
        return self.dist._path  # type: ignore # it exists

    def __repr__(self) -> str:
        return f"{self.__class__.name}(name={self.name}, path={self.path})"


__all__ = [
    "Package",
]