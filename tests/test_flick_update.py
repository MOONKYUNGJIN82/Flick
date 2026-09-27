import hashlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from apps.flick.update import Release, download_release, latest_release


class FlickUpdateTests(unittest.TestCase):
    def test_newer_release_requires_exact_installer_digest_and_url(self):
        digest = hashlib.sha256(b'installer').hexdigest()
        document = {'tag_name': 'flick-v1.0.1', 'assets': [{
            'name': 'Flick.Setup.exe', 'state': 'uploaded', 'size': 9,
            'digest': 'sha256:' + digest,
            'browser_download_url': 'https://github.com/example/flick/releases/download/flick-v1.0.1/Flick.Setup.exe'}]}
        opener = lambda request, timeout: io.BytesIO(json.dumps(document).encode())
        release = latest_release('example/flick', opener=opener)
        self.assertEqual(release.sha256, digest)
        document['tag_name'] = 'flick-v1.0.0'
        self.assertIsNone(latest_release('example/flick', opener=opener))
        document['tag_name'] = 'flick-v1.0.1'
        document['assets'][0]['digest'] = ''
        with self.assertRaises(ValueError):
            latest_release('example/flick', opener=opener)

    def test_download_rejects_corruption_without_leaving_installer(self):
        content = b'installer'
        digest = hashlib.sha256(content).hexdigest()
        release = Release('v1.0.1', 'https://example.invalid/installer', digest, len(content))
        with TemporaryDirectory() as folder:
            target = download_release(release, Path(folder),
                                      opener=lambda request, timeout: io.BytesIO(content))
            self.assertEqual(target.read_bytes(), content)
            target.unlink()
            with self.assertRaises(ValueError):
                download_release(release, Path(folder),
                                 opener=lambda request, timeout: io.BytesIO(b'corrupted'))
            self.assertEqual(list(Path(folder).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
