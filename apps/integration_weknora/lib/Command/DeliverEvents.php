<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Command;

use OCA\IntegrationWeknora\Service\EventDeliveryService;
use Symfony\Component\Console\Command\Command;
use Symfony\Component\Console\Input\InputInterface;
use Symfony\Component\Console\Output\OutputInterface;

/** One bounded delivery pass for a dedicated external worker. */
final class DeliverEvents extends Command {
    public function __construct(private EventDeliveryService $delivery) {
        parent::__construct();
    }

    protected function configure(): void {
        $this->setName('integration_weknora:deliver-events')
            ->setDescription('Deliver due WeKnora event batches once');
    }

    protected function execute(InputInterface $input, OutputInterface $output): int {
        $this->delivery->deliverDue();
        return Command::SUCCESS;
    }
}
