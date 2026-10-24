<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

use OCP\IConfig;
use OCP\Security\ICrypto;

/** Nextcloud-encrypted storage for workspace-scoped onboarding credentials. */
final class ScopedCredentialStore {
    private const CONFIG_KEY = 'weknora_onboarding_credentials';
    private const PREFIX = 'enc:v1:';
    private bool $migrationChecked = false;

    public function __construct(private IConfig $config, private ICrypto $crypto) {
    }

    public function token(string $credentialId): string {
        $all = $this->all();
        $entry = $all[$credentialId] ?? null;
        if (!is_array($entry) || !is_string($entry['token'] ?? null)) {
            throw new \UnexpectedValueException('Connection credential is unavailable');
        }
        return $this->decrypt($entry['token']);
    }

    /** @return array<string,array<string,mixed>> */
    public function all(): array {
        $value = $this->config->getSystemValue(self::CONFIG_KEY, []);
        if (!is_array($value)) {
            return [];
        }
        $out = [];
        foreach ($value as $id => $entry) {
            if (is_string($id) && $id !== '' && is_array($entry)) {
                $out[$id] = $entry;
            }
        }
        return $out;
    }

    /** Replace the complete scoped credential set and remove stale entries. */
    public function replace(array $entries): void {
        $encrypted = [];
        foreach ($entries as $id => $entry) {
            if (!is_string($id) || $id === '' || !is_array($entry) ||
                !is_string($entry['token'] ?? null) || $entry['token'] === '') {
                throw new \InvalidArgumentException('Invalid scoped credential');
            }
            $copy = $entry;
            $copy['token'] = $this->encrypt($entry['token']);
            $encrypted[$id] = $copy;
        }
        $this->config->setSystemValue(self::CONFIG_KEY, $encrypted);
        $this->migrationChecked = true;
    }

    /** Restore the exact previous snapshot after a failed profile switch. */
    public function restoreSnapshot(array $entries): void {
        foreach ($entries as $id => $entry) {
            if (!is_string($id) || !is_array($entry) ||
                !is_string($entry['token'] ?? null) || $entry['token'] === '') {
                throw new \InvalidArgumentException('Invalid credential snapshot');
            }
        }
        $this->config->setSystemValue(self::CONFIG_KEY, $entries);
        $this->migrationChecked = true;
    }

    /** Encrypt legacy plaintext entries once; safe on every request. */
    public function migrateLegacy(): int {
        if ($this->migrationChecked) {
            return 0;
        }
        $this->migrationChecked = true;
        $all = $this->all();
        $changed = 0;
        foreach ($all as $id => &$entry) {
            if (is_string($entry['token'] ?? null) && !str_starts_with($entry['token'], self::PREFIX)) {
                $entry['token'] = $this->encrypt($entry['token']);
                $changed++;
            }
        }
        unset($entry);
        if ($changed > 0) {
            $this->config->setSystemValue(self::CONFIG_KEY, $all);
        }
        return $changed;
    }

    public function encryptionMigrationPending(): bool {
        foreach ($this->all() as $entry) {
            if (is_string($entry['token'] ?? null) && !str_starts_with($entry['token'], self::PREFIX)) {
                return true;
            }
        }
        return false;
    }

    public function staleCount(): int {
        $count = 0;
        foreach ($this->all() as $entry) {
            if (($entry['stale'] ?? false) === true) {
                $count++;
            }
        }
        return $count;
    }

    public function removeIds(array $ids): void {
        $remove = array_fill_keys(array_values(array_filter($ids, 'is_string')), true);
        $all = $this->all();
        foreach (array_keys($remove) as $id) {
            unset($all[$id]);
        }
        $this->config->setSystemValue(self::CONFIG_KEY, $all);
    }

    private function encrypt(string $plaintext): string {
        if ($plaintext === '' || str_starts_with($plaintext, self::PREFIX)) {
            throw new \InvalidArgumentException('Invalid scoped credential plaintext');
        }
        $ciphertext = $this->crypto->encrypt($plaintext);
        if ($ciphertext === '' || $ciphertext === $plaintext || str_contains($ciphertext, $plaintext)) {
            throw new \UnexpectedValueException('Scoped credential encryption failed');
        }
        return self::PREFIX . $ciphertext;
    }

    private function decrypt(string $stored): string {
        if (!str_starts_with($stored, self::PREFIX)) {
            // Development fixtures may still write legacy values directly.
            return $stored;
        }
        $plaintext = $this->crypto->decrypt(substr($stored, strlen(self::PREFIX)));
        if ($plaintext === '') {
            throw new \UnexpectedValueException('Scoped credential decryption failed');
        }
        return $plaintext;
    }
}
