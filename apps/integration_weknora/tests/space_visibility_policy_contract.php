<?php

declare(strict_types=1);

require_once __DIR__ . '/../lib/Service/SpaceVisibilityPolicy.php';

use OCA\IntegrationWeknora\Service\SpaceVisibilityPolicy;

function visibilityCheck(bool $condition, string $message): void {
    if (!$condition) {
        fwrite(STDERR, "FAIL: {$message}\n");
        exit(1);
    }
}

$all = SpaceVisibilityPolicy::normalize(null);
visibilityCheck(SpaceVisibilityPolicy::allows($all, 'alice', [], false), 'Legacy workspaces must remain visible to everyone');
visibilityCheck(SpaceVisibilityPolicy::allows($all, 'admin', [], true), 'Admins must see all workspaces');

$restricted = SpaceVisibilityPolicy::normalize([
    'mode' => SpaceVisibilityPolicy::MODE_RESTRICTED,
    'groups' => ['engineering', 'engineering'],
    'users' => ['alice'],
]);
visibilityCheck($restricted['groups'] === ['engineering'], 'Groups must be deduplicated');
visibilityCheck(SpaceVisibilityPolicy::allows($restricted, 'alice', [], false), 'Direct user grant must be visible');
visibilityCheck(SpaceVisibilityPolicy::allows($restricted, 'bob', ['engineering'], false), 'Group grant must be visible');
visibilityCheck(!SpaceVisibilityPolicy::allows($restricted, 'bob', ['finance'], false), 'Unrelated group must not be visible');
visibilityCheck(SpaceVisibilityPolicy::allows($restricted, 'admin', [], true), 'Admins must always see an authorized workspace');

$adminOnly = SpaceVisibilityPolicy::normalize(['mode' => SpaceVisibilityPolicy::MODE_ADMIN]);
visibilityCheck(!SpaceVisibilityPolicy::allows($adminOnly, 'alice', [], false), 'Admin-only workspace must not be visible to members');
visibilityCheck(SpaceVisibilityPolicy::allows($adminOnly, 'admin', [], true), 'Admin-only workspace must remain visible to admins');

foreach ([
    ['mode' => 'unknown'],
    ['mode' => SpaceVisibilityPolicy::MODE_RESTRICTED],
    ['mode' => SpaceVisibilityPolicy::MODE_ALL, 'groups' => ['engineering']],
    ['mode' => SpaceVisibilityPolicy::MODE_RESTRICTED, 'users' => ['']],
] as $invalid) {
    try {
        SpaceVisibilityPolicy::normalize($invalid);
        visibilityCheck(false, 'Invalid visibility policy must be rejected');
    } catch (\InvalidArgumentException) {
    }
}

fwrite(STDOUT, "space visibility policy contract: PASS\n");
