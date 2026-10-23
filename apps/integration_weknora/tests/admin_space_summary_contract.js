'use strict';

const assert = require('node:assert/strict');
const {
    namedPrincipals,
    principalSummary,
} = require('../js/weknora-admin-summary.js');

const visibility = {
    mode: 'restricted',
    groups: ['admin', 'engineering', 'support', 'finance'],
    users: ['T0001919', 'T0001920'],
    principal_names: {
        groups: [
            {id: 'admin', name: 'Administrators'},
            {id: 'engineering', name: 'Engineering'},
            {id: 'support', name: 'Support'},
            {id: 'finance', name: 'Finance'},
        ],
        users: [
            {id: 'T0001919', name: '刘港'},
            {id: 'T0001920', name: '刘洋'},
        ],
    },
};

const groups = namedPrincipals(visibility, 'group');
assert.equal(groups[0].name, 'Administrators');
assert.equal(groups[3].name, 'Finance');
assert.equal(namedPrincipals(visibility, 'user')[0].name, '刘港');

const compact = principalSummary(visibility);
assert.equal(
    compact.text,
    '组 Administrators、组 Engineering、组 Support +3',
);
assert.match(compact.full, /组 Finance/);
assert.match(compact.full, /用户 刘港/);
assert.equal(compact.count, 6);

const missingNames = principalSummary({
    mode: 'restricted',
    groups: ['legacy-group'],
    users: ['legacy-user'],
});
assert.equal(missingNames.text, '组 legacy-group、用户 legacy-user');
assert.equal(missingNames.full, '组 legacy-group\n用户 legacy-user');

assert.deepEqual(
    principalSummary({mode: 'all'}),
    {text: '所有 Nextcloud 用户', full: '所有 Nextcloud 用户', count: 0},
);
assert.deepEqual(
    principalSummary({mode: 'admin'}),
    {text: '仅 Nextcloud 管理员', full: '仅 Nextcloud 管理员', count: 0},
);

process.stdout.write('admin space summary contract: PASS\n');
