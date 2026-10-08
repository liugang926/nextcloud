<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Command;

use OCA\IntegrationWeknora\Service\PublicationRecoveryReplay;
use Symfony\Component\Console\Command\Command;
use Symfony\Component\Console\Input\InputInterface;
use Symfony\Component\Console\Input\InputOption;
use Symfony\Component\Console\Output\OutputInterface;

final class ReplayRecoveryLedger extends Command {
    public function __construct(private PublicationRecoveryReplay $replay) { parent::__construct(); }
    protected function configure(): void {
        $this->setName('integration_weknora:replay-recovery-ledger')
            ->setDescription('Apply a verified external recovery closure plan in maintenance')
            ->addOption('plan', null, InputOption::VALUE_REQUIRED, 'Absolute private authenticated plan')
            ->addOption('key-file', null, InputOption::VALUE_REQUIRED, 'Absolute private 32-byte journal key')
            ->addOption('inspect-state', null, InputOption::VALUE_NONE, 'Read current receipt and source state without replay');
    }
    private function readPrivateFile(mixed $file, int $maxBytes): string {
        $stat = is_string($file) ? @lstat($file) : false;
        $parent = is_string($file) ? @lstat(dirname($file)) : false;
        if ($stat === false || $parent === false || realpath($file) !== $file ||
            ($parent['mode'] & 0777) !== 0700 || $parent['uid'] !== posix_geteuid() ||
            ($stat['mode'] & 0777) !== 0600 || ($stat['mode'] & 0170000) !== 0100000 ||
            $stat['nlink'] !== 1 || $stat['uid'] !== posix_geteuid() || $stat['size'] > $maxBytes) {
            throw new \InvalidArgumentException('Recovery files must be in a private owned directory without links');
        }
        $handle = @fopen($file, 'rb');
        if ($handle === false) { throw new \InvalidArgumentException('Recovery input is unavailable'); }
        try {
            $actual = fstat($handle); $current = lstat($file);
            foreach (['dev', 'ino', 'uid', 'mode', 'nlink', 'size'] as $field) {
                if ($actual[$field] !== $stat[$field] || $current[$field] !== $stat[$field]) {
                    throw new \DomainException('Recovery input changed during open');
                }
            }
            $bytes = stream_get_contents($handle, $maxBytes + 1);
            if ($bytes === false || strlen($bytes) !== $stat['size']) {
                throw new \DomainException('Recovery input changed while reading');
            }
            return $bytes;
        } finally { fclose($handle); }
    }

    protected function execute(InputInterface $input, OutputInterface $output): int {
        $path = $input->getOption('plan');
        $keyPath = $input->getOption('key-file');
        $planBytes = $this->readPrivateFile($path, 16 * 1024 * 1024);
        $key = $this->readPrivateFile($keyPath, 32);
        if (strlen($key) !== 32) { throw new \InvalidArgumentException('Invalid recovery key size'); }
        $envelope = json_decode($planBytes, true, 32, JSON_THROW_ON_ERROR);
        if (!is_array($envelope) || array_keys($envelope) !== ['plan', 'hmac_sha256']) {
            throw new \InvalidArgumentException('Invalid authenticated recovery envelope');
        }
        // The Python collector signs the exact ASCII canonical plan bytes.
        // The envelope carries those bytes to avoid JSON encoder differences.
        if (!is_string($envelope['plan']) || !is_string($envelope['hmac_sha256']) ||
            !hash_equals(hash_hmac('sha256', $envelope['plan'], $key), $envelope['hmac_sha256'])) {
            throw new \DomainException('Recovery plan authentication failed');
        }
        $plan = json_decode($envelope['plan'], true, 32, JSON_THROW_ON_ERROR);
        $value = $input->getOption('inspect-state') ? $this->replay->inspect($plan) : $this->replay->apply($plan);
        $output->writeln(json_encode($value, JSON_THROW_ON_ERROR));
        return Command::SUCCESS;
    }
}
