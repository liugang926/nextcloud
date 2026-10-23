'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const connection = fs.readFileSync(
    path.join(root, 'lib/Service/ConnectionService.php'), 'utf8');
const directories = fs.readFileSync(
    path.join(root, 'lib/Service/DirectoryPublicationService.php'), 'utf8');
const sidebar = fs.readFileSync(path.join(root, 'src/sidebar.js'), 'utf8');
const bundle = fs.readFileSync(path.join(root, 'js/weknora-sidebar.js'), 'utf8');
const info = fs.readFileSync(path.join(root, 'appinfo/info.xml'), 'utf8');

assert.match(connection, /contract_version' => 3/);
assert.match(connection, /\$contract >= 3/);
assert.match(directories, /'contract_version' => 3/);
assert.match(directories, /nextcloud_source_count/);
assert.match(directories, /kb_mixed_sources/);
assert.match(directories, /kb_unpaired_sources/);
assert.match(sidebar, /已关联 \$\{sourceCount\} 个 Nextcloud 目录/);
assert.match(bundle, /candidate\.nextcloud_source_count/);
assert.match(bundle, /sourceCount > 0/);
assert.match(info, /<version>0\.9\.2<\/version>/);

process.stdout.write('multisource UI contract: PASS\n');
