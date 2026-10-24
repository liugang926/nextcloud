'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const template = fs.readFileSync(path.join(root, 'templates/admin.php'), 'utf8');
const admin = fs.readFileSync(path.join(root, 'js/weknora-admin.js'), 'utf8');
const settings = fs.readFileSync(path.join(root, 'lib/Settings/Admin.php'), 'utf8');

assert.match(template, /<h2>WeKnora 连接<\/h2>/);
assert.match(template, /weknora-setup-origin/);
assert.match(template, /weknora-setup-token/);
assert.match(template, /weknora-setup-space-list/);
assert.match(template, /weknora-setup-disconnect/);
assert.doesNotMatch(template, /weknora-(?:binding|directory-rule|pairing|connection-form|publication)-/);
assert.doesNotMatch(template, /Source diagnostics|Folder bindings|Source pairing|File publication decision/);
assert.doesNotMatch(template, /hidden[^>]*advanced|高级：目录规则|高级：运维与配对/i);
assert.doesNotMatch(admin, /admin\/bindings|admin\/diagnostics|directory-rules|event-connection|publication\//);
assert.doesNotMatch(admin, /localStorage|sessionStorage/);
assert.match(admin, /token\.value = ''/, 'One-time connection credentials must be cleared after use');
assert.doesNotMatch(settings, /bindingsUrl|diagnosticsUrl|linkToRoute/);

// The editing layout remains usable at the normal Nextcloud settings width.
const css = fs.readFileSync(path.join(root, 'css/weknora-admin.css'), 'utf8');
assert.match(css, /\.weknora-admin__space-access \{[\s\S]*?grid-template-columns: minmax\(0, 1fr\);/);

process.stdout.write('connection-only admin boundary and layout contract: PASS\n');
