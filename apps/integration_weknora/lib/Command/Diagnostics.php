<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Command;

use OCA\IntegrationWeknora\Service\ConnectionService;
use OCA\IntegrationWeknora\Service\OperationalStatusService;
use Symfony\Component\Console\Command\Command;
use Symfony\Component\Console\Input\InputInterface;
use Symfony\Component\Console\Output\OutputInterface;

/** Read-only support entry point, separate from ordinary administrator settings. */
final class Diagnostics extends Command {
    public function __construct(
        private OperationalStatusService $status,
        private ConnectionService $connection,
    ) {
        parent::__construct();
    }

    protected function configure(): void {
        $this->setName('integration_weknora:diagnostics')
            ->setDescription('Print privacy-safe WeKnora operational diagnostics as JSON');
    }

    protected function execute(InputInterface $input, OutputInterface $output): int {
        $snapshot = $this->status->snapshot();
        $connection = $this->connection->status();
        $snapshot['connection'] = [
            'connected' => $connection['connected'] ?? false,
            'compatible' => $connection['compatible'] ?? false,
            'binding_allowed' => $connection['binding_allowed'] ?? false,
            'contract_version' => $connection['contract_version'] ?? null,
            'app_version' => $connection['app_version'] ?? '',
            'bundle_version' => $connection['bundle_version'] ?? '',
            'origin_approved' => $connection['origin_approved'] ?? false,
            'credentials_encrypted' => $connection['credentials_encrypted'] ?? false,
            'health' => $connection['health'] ?? null,
        ];
        $output->writeln(json_encode($snapshot,
            JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES));
        return Command::SUCCESS;
    }
}
