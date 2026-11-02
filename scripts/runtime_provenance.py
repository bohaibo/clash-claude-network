"""Explicit read-only official-source verification; never installs or launches anything."""
import argparse
import base64
import datetime
import hashlib
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path
from atomic_files import replace_file


def save_report(output, report):
    temporary = output.with_name(output.name + '.new')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    replace_file(temporary, output)

PROJECT = Path(__file__).resolve().parent.parent
OFFICIAL_HOSTS = {'www.python.org', 'nodejs.org', 'bootstrap.pypa.io'}


def official_url(url):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname not in OFFICIAL_HOSTS or parsed.port not in (None, 443)
            or parsed.username or parsed.password):
        raise ValueError('Only approved official HTTPS sources are allowed')
    return url


class OfficialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        official_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def published_digest(entry, data):
    if entry['method'] == 'official-content-sha256':
        return hashlib.sha256(data).hexdigest()
    if entry['method'] == 'shasums256':
        rows = [line.split() for line in data.decode('utf-8').splitlines()]
        matches = [row[0] for row in rows if len(row) == 2 and row[1] == entry['name']]
        if len(matches) != 1 or not re.fullmatch(r'[a-fA-F0-9]{64}', matches[0]):
            raise ValueError('Missing or ambiguous official checksum')
        return matches[0].lower()
    if entry['method'] == 'sigstore-published-digest':
        digest = json.loads(data)['messageSignature']['messageDigest']
        if digest['algorithm'] != 'SHA2_256':
            raise ValueError('Unexpected digest algorithm')
        value = base64.b64decode(digest['digest'], validate=True)
        if len(value) != 32:
            raise ValueError('Invalid SHA256 digest')
        return value.hex()
    raise ValueError('Unsupported provenance method')


def verify(entry, data):
    official_url(entry['source_url'])
    official_url(entry['download_url'])
    if published_digest(entry, data) != entry['sha256']:
        raise ValueError('Official digest differs from the pinned input: ' + entry['name'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--online', action='store_true', help='explicitly read approved official HTTPS sources')
    args = parser.parse_args(argv)
    if not args.online:
        parser.error('Official network checks require explicit --online; offline tests use fixtures')
    report = {'success': False, 'checked_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'method': 'official-https', 'release_signatures_verified': False, 'files': []}
    output = PROJECT / '.local/runtime-provenance-check.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    # Replace any prior success before reading inputs or constructing a network client.
    save_report(output, report)
    try:
        manifest = json.loads((PROJECT / 'assets/runtime-provenance.json').read_text(encoding='utf-8'))
        if (not isinstance(manifest, dict) or manifest.get('version') != 1
                or not isinstance(manifest.get('files'), list) or not manifest['files']):
            raise ValueError('Invalid provenance manifest')
        names = set()
        for entry in manifest['files']:
            if (not isinstance(entry, dict) or not re.fullmatch(r'[A-Za-z0-9._-]+', str(entry.get('name', '')))
                    or entry['name'] in names or not re.fullmatch(r'[a-f0-9]{64}', str(entry.get('sha256', '')))
                    or entry.get('method') not in ('sigstore-published-digest', 'shasums256', 'official-content-sha256')):
                raise ValueError('Invalid provenance entry')
            names.add(entry['name'])
            official_url(entry['source_url'])
            official_url(entry['download_url'])
        opener = urllib.request.build_opener(OfficialRedirect())
        for entry in manifest['files']:
            with opener.open(official_url(entry['source_url']), timeout=25) as response:
                official_url(response.url)
                data = response.read(6000001)
            if len(data) > 6000000:
                raise ValueError('Unexpected source size')
            verify(entry, data)
            report['files'].append({'name': entry['name'], 'official_digest_matches': True})
        report['success'] = True
    except Exception as exc:
        report['error'] = type(exc).__name__
    save_report(output, report)
    print(json.dumps(report))
    return 0 if report['success'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
