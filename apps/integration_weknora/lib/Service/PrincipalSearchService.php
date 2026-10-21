<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

use OCP\IGroupManager;
use OCP\IUserManager;

/**
 * Produces one typed user/group choice list for the administrator picker.
 *
 * UIDs and GIDs are independent namespaces in Nextcloud, so a unified list
 * must retain the principal type instead of reducing every choice to an ID.
 */
final class PrincipalSearchService {
    private const MAX_CHOICES = 50;

    public function __construct(
        private IGroupManager $groups,
        private IUserManager $users,
    ) {
    }

    /**
     * The legacy group/user arrays remain available while clients migrate to
     * the typed principal list.
     *
     * @return array{
     *   principals:list<array{type:string,id:string,name:string,label:string}>,
     *   groups:list<array{id:string,name:string}>,
     *   users:list<array{id:string,name:string}>
     * }
     */
    public function search(string $query): array {
        $groups = [];
        foreach ($this->groups->search($query, self::MAX_CHOICES, 0) as $group) {
            $groups[] = [
                'id' => $group->getGID(),
                'name' => (string)$group->getDisplayName(),
            ];
        }

        $users = [];
        foreach ($this->users->searchDisplayName($query, self::MAX_CHOICES, 0) as $user) {
            $users[] = [
                'id' => $user->getUID(),
                'name' => (string)$user->getDisplayName(),
            ];
        }

        return [
            'principals' => self::merge($groups, $users, $query),
            'groups' => $groups,
            'users' => $users,
        ];
    }

    /**
     * @param list<array{id:string,name:string}> $groups
     * @param list<array{id:string,name:string}> $users
     * @return list<array{type:string,id:string,name:string,label:string}>
     */
    public static function merge(array $groups, array $users, string $query): array {
        $needle = self::fold(trim($query));
        $choices = [];
        foreach ([['group', $groups], ['user', $users]] as [$type, $entries]) {
            foreach ($entries as $entry) {
                $id = trim((string)($entry['id'] ?? ''));
                if ($id === '') {
                    continue;
                }
                $name = trim((string)($entry['name'] ?? ''));
                if ($name === '') {
                    $name = $id;
                }
                if (!self::matches($needle, $id, $name)) {
                    continue;
                }
                $key = $type . "\0" . $id;
                $choices[$key] = [
                    'type' => $type,
                    'id' => $id,
                    'name' => $name,
                    'label' => $name === $id ? $id : $name . ' (' . $id . ')',
                    '_rank' => self::rank($needle, $id, $name),
                ];
            }
        }

        $choices = array_values($choices);
        usort($choices, static function (array $left, array $right): int {
            return [$left['_rank'], $left['type'], self::fold($left['name']), self::fold($left['id'])]
                <=> [$right['_rank'], $right['type'], self::fold($right['name']), self::fold($right['id'])];
        });

        // Reserve half of the bounded list for each principal type, then fill
        // any unused capacity from the globally ranked remainder. Otherwise a
        // large group directory can hide every user on an empty search.
        $quota = intdiv(self::MAX_CHOICES, 2);
        $selected = [];
        $counts = ['group' => 0, 'user' => 0];
        foreach ($choices as $index => $choice) {
            if ($counts[$choice['type']] >= $quota) {
                continue;
            }
            $selected[$index] = true;
            $counts[$choice['type']]++;
        }
        if (count($selected) < self::MAX_CHOICES) {
            foreach ($choices as $index => $choice) {
                if (count($selected) >= self::MAX_CHOICES) {
                    break;
                }
                if (!isset($selected[$index])) {
                    $selected[$index] = true;
                }
            }
        }

        $out = [];
        foreach ($choices as $index => $choice) {
            if (!isset($selected[$index])) {
                continue;
            }
            unset($choice['_rank']);
            $out[] = $choice;
        }
        return $out;
    }

    private static function rank(string $needle, string $id, string $name): int {
        if ($needle === '') {
            return 6;
        }
        $foldedId = self::fold($id);
        $foldedName = self::fold($name);
        return match (true) {
            $foldedId === $needle => 0,
            $foldedName === $needle => 1,
            str_starts_with($foldedId, $needle) => 2,
            str_starts_with($foldedName, $needle) => 3,
            str_contains($foldedId, $needle) => 4,
            str_contains($foldedName, $needle) => 5,
            default => 6,
        };
    }

    private static function matches(string $needle, string $id, string $name): bool {
        return $needle === ''
            || str_contains(self::fold($id), $needle)
            || str_contains(self::fold($name), $needle);
    }

    private static function fold(string $value): string {
        return function_exists('mb_strtolower')
            ? mb_strtolower($value, 'UTF-8')
            : strtolower($value);
    }
}
