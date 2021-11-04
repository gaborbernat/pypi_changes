from __future__ import annotations

import os
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from typing import Any, Generator, Iterator

from packaging.version import InvalidVersion, Version
from pypi_simple import PyPISimple
from requests import Session
from requests_cache import CachedSession
from rich.progress import BarColumn, Progress, Task, TextColumn, TimeRemainingColumn
from rich.text import Text

from ._cli import Options
from ._pkg import Package

if sys.version_info >= (3, 8):
    from importlib.metadata import PathDistribution
else:
    from importlib_metadata import PathDistribution


def pypi_info(distributions: list[PathDistribution], options: Options) -> Generator[Package, None, None]:
    with ExitStack() as stack:
        enter = stack.enter_context
        session = enter(CachedSession(str(options.cache_path), backend="sqlite", expire_after=options.cache_duration))
        session.remove_expired_responses()  # cleanup old entries
        client = enter(_pypi_client(session))

        progress = Progress(
            "[progress.description]{task.description}",
            BarColumn(),
            TextColumn("[bold magenta] {task.completed}/{task.total}"),
            "[progress.percentage]{task.percentage:>3.0f}%",
            SpeedColumn(),
            TimeRemainingColumn(),
            transient=True,
        )
        enter(progress)
        task = progress.add_task("[red]Acquire release information", total=len(distributions))

        executor = enter(ThreadPoolExecutor(max_workers=options.jobs, thread_name_prefix="version-getter"))
        future_to_url = {executor.submit(one_info, client, session, dist): dist for dist in distributions}
        for future in as_completed(future_to_url):
            dist = future_to_url[future]
            progress.update(task, advance=1)
            try:
                result: Exception | dict[str, Any] | None = future.result()
            except Exception as exc:
                result = exc
            yield Package(dist, result)


class SpeedColumn(TextColumn):
    def __init__(self) -> None:
        super().__init__("[bold cyan]")

    def render(self, task: Task) -> Text:
        if task.speed is None:
            return Text("no speed")
        else:
            return Text(f"{task.speed:.3f} steps/s")


@contextmanager
def _pypi_client(session: Session) -> Iterator[PyPISimple | None]:
    url = os.environ.get("PIP_INDEX_URL")
    if url is not None and url != "https://pypi.org/simple/":
        with PyPISimple(endpoint=url, session=session) as client:
            yield client
    else:
        yield None


def one_info(pypi_client: PyPISimple | None, session: CachedSession, dist: PathDistribution) -> dict[str, Any] | None:
    name: str = dist.metadata["Name"]
    result = _load_from_pypi_json_api(name, session)
    if pypi_client is not None:
        _merge_with_index_server(name, pypi_client, result)
    return result


def _load_from_pypi_json_api(name: str, session: CachedSession) -> dict[str, Any]:
    # ask PyPi - e.g. https://pypi.org/pypi/pip/json, see https://warehouse.pypa.io/api-reference/json/ for more details
    response = session.get(f"https://pypi.org/pypi/{name}/json")
    result: dict[str, Any] = {"releases": {}} if response.status_code == HTTPStatus.NOT_FOUND else response.json()

    # normalize response
    prev_release_at = datetime.now(timezone.utc)
    for a_version, artifact_release in sorted(result["releases"].items(), reverse=True):
        if artifact_release:  # enrich into releases version and transform upload time to python datetime
            for release in artifact_release:
                upload_time = release.get("upload_time_iso_8601")
                if upload_time:
                    upload_time = datetime.fromisoformat(upload_time.replace("Z", "+00:00"))
                else:
                    raise NotImplementedError
                release["upload_time_iso_8601"] = upload_time
                release["version"] = a_version
            prev_release_at = artifact_release[0]["upload_time_iso_8601"]
        else:  # if no releases make up a release time and enrich version
            prev_release_at -= timedelta(seconds=1)
            release = {"packagetype": "sdist", "version": a_version, "upload_time_iso_8601": prev_release_at}
            artifact_release.append(release)

    # order releases in reverse order
    def key(value: tuple[str, list[dict[str, Any]]]) -> tuple[Version, datetime]:
        try:
            version = Version(value[0])
        except InvalidVersion:
            version = Version("0.0.1")
        return version, value[1][0]["upload_time_iso_8601"]

    result["releases"] = dict(sorted(result["releases"].items(), key=key, reverse=True))
    return result


def _merge_with_index_server(name: str, pypi_client: PyPISimple, result: dict[str, Any]) -> None:
    index_info = pypi_client.get_project_page(name)
    if index_info is not None:
        index_releases = defaultdict(list)
        for pkg in index_info.packages:
            release = {"packagetype": pkg.package_type, "version": pkg.version, "upload_time_iso_8601": None}
            index_releases[pkg.version].append(release)

        # insert missing entries at the start
        missing = {ver: values for ver, values in index_releases.items() if ver not in result["releases"]}
        missing.update(result["releases"])
        result["releases"] = missing


__all__ = [
    "pypi_info",
    "Package",
]