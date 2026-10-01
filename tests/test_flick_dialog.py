import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from apps.flick.main import Window


class StoreSequenceDialogTests(unittest.TestCase):
    def test_unique_sequence_opens_after_one_folder_dialog(self):
        with tempfile.TemporaryDirectory() as directory:
            frame = Path(directory) / 'shot.1001.exr'
            frame.touch()
            (Path(directory) / 'shot.1002.exr').touch()
            window = Mock()
            window.tr.side_effect = lambda text: text
            with patch('apps.flick.main.is_app_store_build', return_value=True), \
                 patch('apps.flick.main.QFileDialog.getExistingDirectory', return_value=directory), \
                 patch('apps.flick.main.QFileDialog.getOpenFileName',
                       side_effect=AssertionError('a second native picker opened')):
                Window.choose(window)
            window.open_path.assert_called_once_with(frame)

    def test_multiple_sequences_can_be_chosen_from_list(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'first.0001.png').touch()
            selected = root / 'second.0001.exr'
            selected.touch()
            window = Mock()
            window.tr.side_effect = lambda text: text
            with patch('apps.flick.main.is_app_store_build', return_value=True), \
                 patch('apps.flick.main.QFileDialog.getExistingDirectory', return_value=directory), \
                 patch('apps.flick.main.QFileDialog.getOpenFileName',
                       side_effect=AssertionError('a second native picker opened')), \
                 patch('apps.flick.main.QInputDialog.getItem',
                       return_value=('second.0001.exr', True)):
                Window.choose(window)
            window.open_path.assert_called_once_with(selected)


if __name__ == '__main__':
    unittest.main()
