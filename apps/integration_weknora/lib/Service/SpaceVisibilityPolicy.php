<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

/** Validates and evaluates who may select an authorized WeKnora space. */
final class SpaceVisibilityPolicy {
    public const MODE_ALL = 'all';
    public const MODE_RESTRICTED = 'restricted';
    public const MODE_ADMIN = 'admin';

    /**
     * @return array{mode:string,groups:list<string>,users:list<string>}
     */
    public static function normalize(mixed $raw): array {
        if ($raw === null) {
            return self::policy(self::MODE_ALL);
        }
        if (!is_array($raw)) {
            throw new \InvalidArgumentException('Invalid workspace visibility');
        }
        $mode = $raw['mode'] ?? self::MODE_ALL;
        if (!is_string($mode) || !in_array($mode, [
            self::MODE_ALL,
            self::MODE_RESTRICTED,
            self::MODE_ADMIN,
        ], true)) {
            throw new \InvalidArgumentException('Invalid workspace visibility mode');
        }
        $groups = self::principals($raw['groups'] ?? []);
        $users = self::principals($raw['users'] ?? []);
        if ($mode !== self::MODE_RESTRICTED && ($groups !== [] || $users !== [])) {
            throw new \InvalidArgumentException('Only restricted visibility may name principals');
        }
        if ($mode === self::MODE_RESTRICTED && $groups === [] && $users === []) {
            throw new \InvalidArgumentException('Restricted workspace visibility requires a user or group');
        }
        return self::policy($mode, $groups, $users);
    }

    /**
     * @param list<string> $userGroups
     */
    public static function allows(
        array $policy,
        string $uid,
        array $userGroups,
        bool $admin,
    ): bool {
        if ($admin) {
            return true;
        }
        $mode = $policy['mode'] ?? self::MODE_ALL;
        if ($mode === self::MODE_ALL) {
            return true;
        }
        if ($mode !== self::MODE_RESTRICTED || $uid === '') {
            return false;
        }
        if (in_array($uid, $policy['users'] ?? [], true)) {
            return true;
        }
        return array_intersect($userGroups, $policy['groups'] ?? []) !== [];
    }

    /**
     * @return list<string>
     */
    private static function principals(mixed $raw): array {
        if (!is_array($raw) || !array_is_list($raw) || count($raw) > 64) {
            throw new \InvalidArgumentException('Invalid workspace visibility principals');
        }
        $out = [];
        foreach ($raw as $principal) {
            if (!is_string($principal)) {
                throw new \InvalidArgumentException('Invalid workspace visibility principal');
            }
            $principal = trim($principal);
            if ($principal === '' || strlen($principal) > 255 ||
                preg_match('/[\x00-\x1f\x7f]/', $principal)) {
                throw new \InvalidArgumentException('Invalid workspace visibility principal');
            }
            $out[$principal] = true;
        }
        $out = array_keys($out);
        sort($out, SORT_STRING);
        return $out;
    }

    /**
     * @param list<string> $groups
     * @param list<string> $users
     * @return array{mode:string,groups:list<string>,users:list<string>}
     */
    private static function policy(string $mode, array $groups = [], array $users = []): array {
        return ['mode' => $mode, 'groups' => $groups, 'users' => $users];
    }
}
