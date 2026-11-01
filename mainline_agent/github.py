"""GitHub networking and immutable source snapshots. AI remains in local Ollama."""
import base64
import difflib
import hashlib
import json
import os
import re
from pathlib import Path
from .discovery import decode_source
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise ValueError('GitHub redirects are disabled. Check the repository name.')


class GitHub:
    def __init__(self, repository, token=None):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository) or any(x in ('.', '..') for x in repository.split('/')):
            raise ValueError('GitHub repository must be OWNER/REPO.')
        self.repository = repository
        self.root = '/repos/' + repository
        self.token = token or os.environ.get('GITHUB_TOKEN')
        self.opener = build_opener(NoRedirect())

    def request(self, method, path, data=None, params=None):
        if method != 'GET' or data is not None:
            raise ValueError('GitHub access is read-only. Comments, issues and labels are disabled.')
        if not path.startswith(self.root + '/') and path != '/user':
            raise ValueError('GitHub request is outside the configured repository.')
        url = 'https://api.github.com' + path
        if params:
            url += '?' + urlencode(params)
        headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'mainline-agent',
                   'X-GitHub-Api-Version': '2022-11-28'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        request = Request(url, method=method, headers=headers,
                          data=json.dumps(data).encode() if data is not None else None)
        try:
            with self.opener.open(request, timeout=60) as response:
                raw = response.read(12_000_001)
                if len(raw) > 12_000_000:
                    raise ValueError('GitHub response exceeds the inspection limit.')
                return json.loads(raw)
        except HTTPError as exc:
            raise ValueError('GitHub HTTP %s. Check repository permissions, rate limits and configuration.' % exc.code) from exc
        except (URLError, OSError) as exc:
            raise ValueError('GitHub connection failed. The monitor will retry on the next poll.') from exc

    def pages(self, path, params=None):
        for page in range(1, 101):
            query = dict(params or {}, per_page=100, page=page)
            values = self.request('GET', path, params=query)
            if not isinstance(values, list):
                raise ValueError('Invalid GitHub collection response.')
            yield from values
            if len(values) < 100:
                return
        raise ValueError('GitHub collection exceeds 100 pages. Analysis was not completed.')


class GitHubSnapshot:
    def __init__(self, api, base, head, cache, allowed_paths=None):
        for sha in (base, head):
            if not isinstance(sha, str) or not re.fullmatch(r'[a-f0-9]{40,64}', sha):
                raise ValueError('GitHub snapshot requires immutable commit SHAs.')
        self.api, self.head, self.base = api, head, base
        self.path = 'github:' + api.repository
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.allowed_paths = allowed_paths
        self.trees = {}
        for sha in {base, head}:
            commit = api.request('GET', api.root + '/git/commits/' + sha)
            tree = api.request('GET', api.root + '/git/trees/' + commit['tree']['sha'], params={'recursive': 1})
            if tree.get('truncated'):
                raise ValueError('GitHub truncated the source tree. Refusing an incomplete comparison.')
            self.trees[sha] = {entry['path']: entry for entry in tree['tree']
                               if entry['type'] == 'blob' and entry['mode'] in ('100644', '100755')}

    def files(self, sha):
        return sorted(self.trees[sha])

    def read(self, sha, path, limit=2_000_000):
        entry = self.trees[sha][path]
        if entry.get('size', limit + 1) > limit:
            raise ValueError('File exceeds inspection limit: ' + path)
        blob = entry['sha']
        if not re.fullmatch(r'[a-f0-9]{40,64}', blob):
            raise ValueError('Invalid GitHub blob SHA.')
        cached = self.cache / blob
        if cached.exists():
            if cached.stat().st_size > limit:
                raise ValueError('Cached blob exceeds inspection limit.')
            data = cached.read_bytes()
        else:
            response = self.api.request('GET', self.api.root + '/git/blobs/' + blob)
            if response.get('encoding') != 'base64':
                raise ValueError('Unsupported GitHub blob encoding.')
            data = base64.b64decode(response['content'])
        if len(data) > limit or b'\0' in data:
            raise ValueError('Oversized or binary file: ' + path)
        header = ('blob %s\0' % len(data)).encode()
        algorithm = hashlib.sha1 if len(blob) == 40 else hashlib.sha256
        if algorithm(header + data).hexdigest() != blob:
            raise ValueError('GitHub blob checksum mismatch. Remove the corrupt blob cache before retrying.')
        if not cached.exists():
            cached.write_bytes(data)
        return decode_source(data, path)

    def changes(self, base):
        before, after = self.trees[base], self.trees[self.head]
        return sorted(path for path in before.keys() | after.keys()
                      if (self.allowed_paths is None or path in self.allowed_paths)
                      and before.get(path) != after.get(path))

    def patch(self, base, path):
        old = self.read(base, path) if path in self.trees[base] else ''
        new = self.read(self.head, path) if path in self.trees[self.head] else ''
        return '\n'.join(difflib.unified_diff(old.splitlines(), new.splitlines(),
                         fromfile='a/' + path, tofile='b/' + path, lineterm=''))
