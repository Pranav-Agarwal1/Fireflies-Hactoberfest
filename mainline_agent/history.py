"""Validate locally supplied discussion exports. Never fetch URLs."""
import json
from pathlib import Path
from urllib.parse import urlparse


def read_json(path, limit=4_000_000):
    with Path(path).open('rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('Local JSON exceeds the %s-byte input limit.' % limit)
    return json.loads(raw.decode('utf-8'))


def validate_history(data):
    if not isinstance(data, dict) or not isinstance(data.get('items'), list):
        raise ValueError('History JSON must contain an items array.')
    if len(data['items']) > 5000:
        raise ValueError('History export exceeds 5000 sources; split the file.')
    warnings = data.get('warnings', [])
    if not isinstance(warnings, list) or not all(isinstance(x, str) for x in warnings):
        raise ValueError('History warnings must be an array of strings.')
    seen = set()
    for item in data['items']:
        if not isinstance(item, dict) or not all(isinstance(item.get(k), str) and item[k].strip() for k in ('id', 'url', 'text')):
            raise ValueError('History items require nonempty string id, url and text.')
        if item['id'] in seen or item['id'].startswith(('code-', 'doc-')):
            raise ValueError('Duplicate or reserved history source id.')
        seen.add(item['id'])
        url = urlparse(item['url'])
        if url.scheme != 'https' or not url.hostname or url.username or url.password:
            raise ValueError('History URLs must be valid HTTPS references; they are never fetched.')
        if len(item['text']) > 100000:
            raise ValueError('History source exceeds 100000 characters.')
        for field in ('created_at', 'updated_at'):
            if item.get(field) is not None and not isinstance(item[field], str):
                raise ValueError('History timestamps must be strings or null.')
    return data
