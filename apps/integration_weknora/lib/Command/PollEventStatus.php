<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Command;

use OCA\IntegrationWeknora\Service\EventAppliedStatusService;
use Symfony\Component\Console\Command\Command;
use Symfony\Component\Console\Input\InputInterface;
use Symfony\Component\Console\Output\OutputInterface;

/** One bounded applied-watermark pass for a dedicated external worker. */
final class PollEventStatus extends Command {
    public function __construct(private EventAppliedStatusService $appliedStatus) {
        parent::__construct();
    }

    protected function configure(): void {
        $this->setName('integration_weknora:poll-event-status')
            ->setDescription('Verify due WeKnora applied watermarks once');
    }

    protected function execute(InputInterface $input, OutputInterface $output): int {
        $this->appliedStatus->pollDue();
        return Command::SUCCESS;
    }
}
