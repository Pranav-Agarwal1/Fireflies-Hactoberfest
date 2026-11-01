import json
import tempfile
import unittest
from pathlib import Path
from mainline_agent.history import read_json, validate_history


class HistoryTests(unittest.TestCase):
    def item(self, **changes):
        item = dict(id='discussion-1', url='https://example.org/discussion/1', text='We decided to retain the C parser.')
        item.update(changes)
        return item

    def test_valid_export_preserves_unicode(self):
        data = {'items': [self.item(text='Décision: conserver le parseur')], 'warnings': ['Offline export']}
        self.assertEqual(validate_history(data), data)

    def test_invalid_snapshots_rejected(self):
        values = [[], {}, {'items': [{}]}, {'items': [self.item(url='file:///etc/passwd')]},
                  {'items': [self.item(url='https://')]}, {'items': [self.item(url='https://user:pass@example.org')]},
                  {'items': [self.item(id='code-head')]}, {'items': [self.item(), self.item()]},
                  {'items': [self.item(text='   ')]}, {'items': [self.item(text='x'*100001)]},
                  {'items': [], 'warnings': 'bad'}, {'items': [self.item(created_at=123)]}]
        for value in values:
            with self.subTest(value=str(value)[:80]), self.assertRaises(ValueError):
                validate_history(value)

    def test_local_json_limits_and_malformed_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'history.json'
            path.write_text('{"items": []}')
            self.assertEqual(read_json(path), {'items': []})
            with self.assertRaisesRegex(ValueError, 'limit'):
                read_json(path, limit=3)
            path.write_text('{oops')
            with self.assertRaises(ValueError):
                read_json(path)
            path.write_bytes(b'\xff\xfe')
            with self.assertRaises(ValueError):
                read_json(path)
