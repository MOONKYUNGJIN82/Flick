"""GitHub Releases updater. Network and hashing run outside the UI thread."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import os
import platform
import re
import sys
import urllib.request

VERSION = '1.0.0'
def asset_name():
    if sys.platform == 'darwin':
        arch = 'arm64' if platform.machine().lower() in ('arm64', 'aarch64') else 'x86_64'
        return f'Flick-macOS-{arch}.zip'
    return 'Flick.Setup.exe'


ASSET_NAME = asset_name()
def configured_repository():
    override = os.environ.get('FLICK_GITHUB_REPOSITORY')
    if override is not None:
        return override
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[2]))
    try:
        return json.loads((root / 'flick-release.json').read_text(encoding='utf-8'))['repository']
    except (OSError, ValueError, KeyError, TypeError):
        return ''


REPOSITORY = configured_repository()
USER_AGENT = 'Flick/' + VERSION


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    sha256: str
    size: int


def version_parts(value):
    match = re.fullmatch(r'(?:flick-)?v?(\d+)\.(\d+)\.(\d+)', value)
    if not match:
        raise ValueError('Invalid release version')
    return tuple(map(int, match.groups()))


def latest_release(repository=REPOSITORY, current=VERSION, opener=urllib.request.urlopen):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise ValueError('GitHub repository is not configured')
    request = urllib.request.Request(
        f'https://api.github.com/repos/{repository}/releases/latest',
        headers={'Accept': 'application/vnd.github+json', 'User-Agent': USER_AGENT})
    with opener(request, timeout=10) as response:
        data = json.load(response)
    version = data['tag_name']
    if version_parts(version) <= version_parts(current):
        return None
    for asset in data['assets']:
        if asset['name'] == ASSET_NAME and asset.get('state') == 'uploaded':
            digest = asset.get('digest', '')
            url = asset['browser_download_url']
            expected = f'https://github.com/{repository}/releases/download/{version}/{ASSET_NAME}'
            if not re.fullmatch(r'sha256:[0-9a-fA-F]{64}', digest) or url != expected:
                raise ValueError('Release installer has no valid SHA-256 digest or URL')
            return Release(version, url, digest[7:].lower(), int(asset['size']))
    raise ValueError('Release installer is missing')


def download_release(release: Release, directory: Path, opener=urllib.request.urlopen):
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f'Flick-{release.version}-{ASSET_NAME}'
    temporary = directory / f'Flick-{release.version}-{ASSET_NAME}.part'
    if target.is_file() and target.stat().st_size == release.size:
        with target.open('rb') as existing:
            if hashlib.file_digest(existing, 'sha256').hexdigest() == release.sha256:
                return target
    request = urllib.request.Request(release.url, headers={'User-Agent': USER_AGENT})
    digest = hashlib.sha256()
    received = 0
    try:
        with opener(request, timeout=30) as response, temporary.open('wb') as output:
            while chunk := response.read(1024 * 1024):
                received += len(chunk)
                if received > release.size:
                    raise ValueError('Installer exceeds published size')
                digest.update(chunk)
                output.write(chunk)
        if received != release.size or digest.hexdigest() != release.sha256:
            raise ValueError('Installer checksum mismatch')
        temporary.replace(target)
        return target
    finally:
        temporary.unlink(missing_ok=True)


def update_dir():
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Caches' / 'Flick' / 'updates'
    return Path(os.environ.get('LOCALAPPDATA', Path.home() / '.cache')) / 'Flick' / 'updates'
