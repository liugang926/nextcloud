'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const css = fs.readFileSync(path.join(root, 'css/weknora-admin.css'), 'utf8');
const admin = fs.readFileSync(path.join(root, 'js/weknora-admin.js'), 'utf8');

assert.match(
    css,
    /\.weknora-admin__space-list-header--editing,\s*\.weknora-admin__space-row--editing \{[\s\S]*?min-width: 640px;/,
    'Editing rows must fit the normal Nextcloud settings content width',
);
assert.doesNotMatch(css, /min-width: 940px;/, 'Editing rows must not retain the oversized minimum width');
assert.match(
    css,
    /\.weknora-admin__space-access \{[\s\S]*?grid-template-columns: minmax\(0, 1fr\);/,
    'Visibility controls must stack inside their compact column',
);
assert.match(
    css,
    /\.weknora-admin__space-identity--editing \.weknora-admin__space-badge \{\s*display: none;/,
    'Editing rows must not duplicate the allow state beside the space name',
);
assert.doesNotMatch(admin, /space-card/, 'The administration view must not regress to cards');
assert.match(
    admin,
    /checkbox\.setAttribute\('aria-label',\s*`允许 \$\{space\.tenant_name \|\| space\.name \|\| space\.tenant_id\} 供 Nextcloud 目录使用`\);/,
    'The compact allow checkbox must retain an explicit accessible label',
);
assert.match(admin, /identity\.title = name;/, 'Truncated space names must expose their full value');
assert.match(admin, /templateCell\.title = templateName;/, 'Truncated processing templates must expose their full value');

process.stdout.write('admin layout contract: PASS\n');
