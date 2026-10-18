#!/usr/bin/env python3
"""Verify the installed HTTP backend without printing credentials or tokens."""
import base64
import hashlib
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import re
import secrets
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import zipfile


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class InitialStateParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.document = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == 'input' and attributes.get('id') == 'initial-state-richdocuments-document':
            self.document = json.loads(base64.b64decode(attributes['value'], validate=True))


def main():
    root = Path('/srv/nextcloud-production')
    compose = ['docker', 'compose', '--env-file', str(root / 'secrets/images.env'),
               '-p', 'nextcloud-production', '-f',
               str(root / 'current/deploy/production/compose.yaml')]
    config = json.loads(subprocess.check_output(compose + ['config', '--format', 'json']))
    env = config['services']['nextcloud']['environment']
    user = env['NEXTCLOUD_ADMIN_USER']
    password = (root / 'secrets/nextcloud_admin_password').read_text().strip()
    auth = base64.b64encode((user + ':' + password).encode()).decode()
    domain = env['OVERWRITEHOST']
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    base = 'http://10.106.1.128:18082'

    def request(path, method='GET', body=None, authenticated=True, content_type=None):
        headers = {'Host': domain, 'OCS-APIRequest': 'true', 'Accept': 'application/json'}
        if authenticated:
            headers['Authorization'] = 'Basic ' + auth
        if content_type:
            headers['Content-Type'] = content_type
        req = urllib.request.Request(base + path, data=body, method=method, headers=headers)
        try:
            with opener.open(req, timeout=60) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            # Never print bodies that can contain a WOPI token or user data.
            raise RuntimeError(f'Backend {method} request failed with HTTP {error.code}') from None

    status, _, body = request('/status.php', authenticated=False)
    state = json.loads(body)
    assert status == 200 and state['installed'] and not state['maintenance'] and not state['needsDbUpgrade']
    status, _, body = request('/ocs/v2.php/cloud/user?format=json')
    assert status == 200 and json.loads(body)['ocs']['data']['id'] == user
    status, _, body = request('/hosting/discovery', authenticated=False)
    assert status == 200 and b'wopi-discovery' in body
    status, _, body = request('/hosting/capabilities', authenticated=False)
    assert status == 200 and isinstance(json.loads(body), dict)

    # A real DOCX with harmless synthetic text exercises file persistence and
    # the actual Office parser; it is removed in finally even when a check fails.
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        archive.writestr('_rels/.rels', '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        archive.writestr('word/document.xml', '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Nextcloud production Office verification.</w:t></w:r></w:p></w:body></w:document>')
    document = output.getvalue()
    path = '/remote.php/dav/files/' + urllib.parse.quote(user, safe='') + '/production-verification-' + secrets.token_hex(8) + '.docx'
    created = False
    try:
        status, headers, _ = request(path, 'PUT', document, content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document')
        assert status == 201
        created = True
        status, _, body = request(path)
        assert status == 200 and hashlib.sha256(body).digest() == hashlib.sha256(document).digest()
        file_id = next(value for name, value in headers.items() if name.lower() == 'oc-fileid')
        file_id = int(re.match(r'[0-9]+', file_id).group())
        status, _, body = request('/ocs/v2.php/apps/richdocuments/api/v1/document?format=json', 'POST',
                                  urllib.parse.urlencode({'fileId': file_id}).encode(),
                                  content_type='application/x-www-form-urlencoded')
        direct = json.loads(body)['ocs']['data']['url']
        direct_url = urllib.parse.urlsplit(direct)
        assert direct_url.scheme == 'https' and direct_url.netloc == domain
        assert '/apps/richdocuments/direct/' in direct_url.path
        # This is a one-use public route; no cookie/session or Basic header is
        # needed after the authenticated OCS call creates it. Error templates
        # can also return HTTP 200, so require the actual document/WOPI state.
        status, _, body = request(direct_url.path, authenticated=False)
        assert status == 200
        parser = InitialStateParser()
        parser.feed(body.decode())
        state = parser.document
        assert isinstance(state, dict) and state.get('token') and state.get('direct') is True
        assert state.get('fileId', '').startswith(str(file_id) + '_')
        editor_url = urllib.parse.urlsplit(state.get('urlsrc', ''))
        assert editor_url.scheme == 'https' and editor_url.netloc == domain
        # Exercise the configured private WOPI callback from the Office service.
        # Tokens enter on stdin and neither response bodies nor curl stderr are
        # printed by this verifier.
        callback_url = 'http://nextcloud/index.php/apps/richdocuments/wopi/files/' + urllib.parse.quote(state['fileId'], safe='')
        callback_url += '?' + urllib.parse.urlencode({'access_token': state['token']})
        callback_config = 'fail\nsilent\nshow-error\nmax-time = 60\nurl = ' + json.dumps(callback_url) + '\n'
        callback = subprocess.run(compose + ['exec', '-T', 'office', 'curl', '--config', '-'],
                                  input=callback_config.encode(),
                                  capture_output=True, timeout=90)
        assert callback.returncode == 0
        info = json.loads(callback.stdout)
        assert info.get('BaseFileName') == urllib.parse.unquote(path.rsplit('/', 1)[1])
        assert info.get('Size') == len(document)
        # Convert inside the Office container. Conversion is deliberately not
        # exposed as a new public route for this verification.
        shell = 'set -eu; f=$(mktemp --suffix=.docx); trap \'rm -f "$f"\' EXIT; cat > "$f"; curl -fsS --max-time 90 -F "data=@$f" -F "format=pdf" http://127.0.0.1:9980/cool/convert-to/pdf'
        result = subprocess.run(compose + ['exec', '-T', 'office', 'bash', '-c', shell],
                                input=document, capture_output=True, timeout=120)
        assert result.returncode == 0 and result.stdout.startswith(b'%PDF-') and len(result.stdout) > 200
    finally:
        if created:
            status, _, _ = request(path, 'DELETE')
            assert status == 204
    print(json.dumps({'status': 'pass', 'admin_authentication': True, 'webdav_roundtrip': True,
                      'office_discovery': True, 'office_direct_edit_route': True,
                      'office_private_wopi_callback': True,
                      'office_docx_to_pdf': True, 'synthetic_file_removed': True,
                      'external_https_browser_edit': 'requires operator reverse proxy'}))


if __name__ == '__main__':
    main()
