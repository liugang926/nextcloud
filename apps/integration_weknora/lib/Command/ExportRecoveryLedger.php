<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Command;

use OCA\IntegrationWeknora\Service\PublicationRecoveryLedger;
use Symfony\Component\Console\Command\Command;
use Symfony\Component\Console\Input\InputInterface;
use Symfony\Component\Console\Input\InputOption;
use Symfony\Component\Console\Output\OutputInterface;

final class ExportRecoveryLedger extends Command {
    public function __construct(private PublicationRecoveryLedger $ledger) { parent::__construct(); }

    protected function configure(): void {
        $this->setName('integration_weknora:export-recovery-ledger')
            ->setDescription('Export one continuous publication recovery journal page')
            ->addOption('after', null, InputOption::VALUE_REQUIRED, 'Last retained sequence', '0');
    }

    protected function execute(InputInterface $input, OutputInterface $output): int {
        $after = filter_var($input->getOption('after'), FILTER_VALIDATE_INT,
            ['options' => ['min_range' => 0, 'max_range' => PHP_INT_MAX]]);
        if ($after === false) { throw new \InvalidArgumentException('Invalid recovery cursor'); }
        $output->writeln(json_encode($this->ledger->page($after), JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES));
        return Command::SUCCESS;
    }
}
