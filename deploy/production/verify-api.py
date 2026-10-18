#!/usr/bin/env python3
"""Verify the installed HTTP backend without printing credentials or tokens."""
import base64
import hashlib
from html.parser import HTMLParser
import io
import json
from pathlib import Path, PurePosixPath
import re
import secrets
import subprocess
import tarfile
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


def synthetic_docx():
    """A Unicode DOCX exercises the Office runtime's locale and document parser."""
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        archive.writestr('_rels/.rels', '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        archive.writestr('word/document.xml', '<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Nextcloud 生产环境 Office 中文验证。</w:t></w:r></w:p></w:body></w:document>')
    return output.getvalue()


def office_data_owner(image):
    """Inspect a directory without assuming the distroless image has stat."""
    container = subprocess.check_output(
        ['docker', 'create', '--pull=never', '--network=none', image], text=True).strip()
    try:
        archive = subprocess.check_output(['docker', 'cp', container + ':/opt/cool/child-roots', '-'])
        with tarfile.open(fileobj=io.BytesIO(archive), mode='r:*') as contents:
            roots = [member for member in contents
                     if PurePosixPath(member.name).parts == ('child-roots',) and member.isdir()]
        if len(roots) != 1:
            raise RuntimeError('Office image has no unambiguous child-roots directory')
        return str(roots[0].uid) + ':' + str(roots[0].gid)
    finally:
        subprocess.run(['docker', 'rm', '--force', container], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def office_curl(container, helper_image, arguments, data=b'', timeout=90, cgroup_parent=None):
    """Use the approved Nextcloud image's curl in Office's network namespace.

    No production data or secrets are mounted. Input can contain a WOPI token,
    so callers receive captured output and must never print curl's response or
    stderr. Explicit cleanup also removes a helper after a Docker CLI timeout.
    """
    name = 'nextcloud-office-probe-' + secrets.token_hex(8)
    command = ['docker', 'run', '--rm', '--pull=never', '--interactive', '--name', name,
               '--network=container:' + container, '--user=33:33', '--read-only',
               '--cap-drop=ALL', '--security-opt=no-new-privileges', '--log-driver=none',
               '--cpus=0.25', '--memory=128m', '--pids-limit=64']
    if cgroup_parent:
        command.append('--cgroup-parent=' + cgroup_parent)
    command += ['--entrypoint=curl', helper_image, *arguments]
    try:
        return subprocess.run(command, input=data, capture_output=True, timeout=timeout)
    finally:
        subprocess.run(['docker', 'rm', '--force', name], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def convert_docx(container, helper_image, document, filename, cgroup_parent=None):
    # curl reads the multipart document directly from stdin. The loopback
    # conversion route is reached from Office's own network namespace.
    result = office_curl(container, helper_image,
                         ['--fail', '--silent', '--show-error', '--max-time', '90',
                          '--form', 'data=@-;filename=' + filename,
                          '--form-string', 'format=pdf',
                          'http://127.0.0.1:9980/cool/convert-to/pdf'],
                         data=document, timeout=120, cgroup_parent=cgroup_parent)
    if result.returncode != 0 or not result.stdout.startswith(b'%PDF-') or len(result.stdout) <= 200:
        raise RuntimeError('Office failed to convert the synthetic Unicode DOCX to PDF')
    return result.stdout


def main():
    root = Path('/srv/nextcloud-production')
    compose = ['docker', 'compose', '--env-file', str(root / 'secrets/images.env'),
               '-p', 'nextcloud-production', '-f',
               str(root / 'current/deploy/production/compose.yaml')]
    config = json.loads(subprocess.check_output(compose + ['config', '--format', 'json']))
    env = config['services']['nextcloud']['environment']
    helper_image = config['services']['nextcloud']['image']
    office_container = subprocess.check_output(compose + ['ps', '-q', 'office'], text=True).strip()
    assert re.fullmatch(r'[0-9a-f]{12,64}', office_container), 'Expected one running Office container'
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
    document = synthetic_docx()
    filename = '生产环境 Office 中文验证-' + secrets.token_hex(8) + '.docx'
    path = '/remote.php/dav/files/' + urllib.parse.quote(user, safe='') + '/' + urllib.parse.quote(filename, safe='')
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
        callback = office_curl(office_container, helper_image, ['--config', '-'],
                               data=callback_config.encode(), cgroup_parent='nextcloud-production.slice')
        assert callback.returncode == 0
        info = json.loads(callback.stdout)
        assert info.get('BaseFileName') == urllib.parse.unquote(path.rsplit('/', 1)[1])
        assert info.get('Size') == len(document)
        convert_docx(office_container, helper_image, document, filename,
                     cgroup_parent='nextcloud-production.slice')
    finally:
        if created:
            status, _, _ = request(path, 'DELETE')
            assert status == 204
    print(json.dumps({'status': 'pass', 'admin_authentication': True, 'webdav_roundtrip': True,
                      'office_discovery': True, 'office_direct_edit_route': True,
                      'office_private_wopi_callback': True,
                      'office_docx_to_pdf': True, 'office_unicode_filename_and_content': True,
                      'synthetic_file_removed': True,
                      'external_https_browser_edit': 'requires operator reverse proxy'}))


if __name__ == '__main__':
    main()
