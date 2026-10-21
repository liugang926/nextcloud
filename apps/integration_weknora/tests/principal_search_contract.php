<?php

declare(strict_types=1);

require_once __DIR__ . '/../lib/Service/PrincipalSearchService.php';

use OCA\IntegrationWeknora\Service\PrincipalSearchService;

function principalCheck(bool $condition, string $message): void {
    if (!$condition) {
        fwrite(STDERR, "FAIL: {$message}\n");
        exit(1);
    }
}

$choices = PrincipalSearchService::merge([
    ['id' => 'engineering', 'name' => 'Engineering'],
    ['id' => 'shared', 'name' => 'Shared group'],
    ['id' => 'engineering', 'name' => 'Duplicate group'],
], [
    ['id' => 'alice', 'name' => 'Alice Zhang'],
    ['id' => 'shared', 'name' => 'Shared user'],
], 'shared');

principalCheck(count($choices) === 2, 'Same UID and GID must remain two typed principals');
principalCheck($choices[0]['type'] === 'group' && $choices[0]['id'] === 'shared', 'Group choice must retain its type');
principalCheck($choices[1]['type'] === 'user' && $choices[1]['id'] === 'shared', 'User choice must retain its type');
principalCheck($choices[0]['label'] === 'Shared group (shared)', 'Group label must identify display name and GID');
principalCheck($choices[1]['label'] === 'Shared user (shared)', 'User label must identify display name and UID');

$ranked = PrincipalSearchService::merge([], [
    ['id' => 'zhang-alice', 'name' => 'Alice Zhang'],
    ['id' => 'alice', 'name' => 'Alice'],
    ['id' => 'engineering-alice', 'name' => 'Engineering Alice'],
], 'alice');

principalCheck(array_column($ranked, 'id') === ['alice', 'zhang-alice', 'engineering-alice'],
    'Exact ID and name-prefix matches must rank before substring matches');

$unicode = PrincipalSearchService::merge([], [
    ['id' => 'T0001919', 'name' => '刘港'],
    ['id' => 'T0001920', 'name' => '刘洋'],
], '刘');
principalCheck(count($unicode) === 2 && $unicode[0]['type'] === 'user',
    'Unicode display-name search choices must remain grouped as users');

$largeGroups = [];
$largeUsers = [];
for ($index = 0; $index < 60; $index++) {
    $largeGroups[] = ['id' => 'group-' . str_pad((string)$index, 2, '0', STR_PAD_LEFT), 'name' => 'Group ' . $index];
    $largeUsers[] = ['id' => 'user-' . str_pad((string)$index, 2, '0', STR_PAD_LEFT), 'name' => 'User ' . $index];
}
principalCheck(count(PrincipalSearchService::merge($largeGroups, $largeUsers, '')) === 50,
    'Unified autocomplete must stay within its bounded result limit');

fwrite(STDOUT, "principal search contract: PASS\n");
