"""Build Flick's Windows folder, installer and SHA-256 release sidecar."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
VERSION = '1.0.0'


def run(*args):
    subprocess.run([str(arg) for arg in args], cwd=ROOT, check=True)


def main():
    tag = os.environ.get('GITHUB_REF_NAME', '')
    if tag and tag != f'flick-v{VERSION}':
        raise RuntimeError(f'Release tag {tag} does not match Flick {VERSION}')
    repository = os.environ.get('FLICK_GITHUB_REPOSITORY', '')
    if repository:
        (ROOT / 'flick-release.json').write_text(
            json.dumps({'repository': repository}) + '\n', encoding='utf-8')
    run(sys.executable, '-m', 'unittest', 'tests/test_flick.py', 'tests/test_flick_update.py')
    run(sys.executable, '-m', 'PyInstaller', '--noconfirm', ROOT / 'Flick.spec')
    compiler = os.environ.get('INNO_SETUP_COMPILER',
                              'C:/Program Files (x86)/Inno Setup 6/ISCC.exe')
    run(compiler, '/Qp', f'/DAppVersion={VERSION}', ROOT / 'packaging/flick.iss')
    installer = ROOT / 'dist/Flick.Setup.exe'
    with installer.open('rb') as binary:
        digest = hashlib.file_digest(binary, 'sha256').hexdigest()
    installer.with_suffix('.exe.sha256').write_text(
        f'{digest}  {installer.name}\n', encoding='ascii')
    print(f'{installer}\nSHA-256: {digest}')


if __name__ == '__main__':
    main()
