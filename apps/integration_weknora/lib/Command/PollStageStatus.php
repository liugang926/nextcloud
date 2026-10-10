<?php
declare(strict_types=1);
namespace OCA\IntegrationWeknora\Command;
use OCA\IntegrationWeknora\Service\RemoteStageStatusService;
use Symfony\Component\Console\Command\Command;
use Symfony\Component\Console\Input\InputInterface;
use Symfony\Component\Console\Output\OutputInterface;
final class PollStageStatus extends Command {
    public function __construct(private RemoteStageStatusService $statuses) { parent::__construct(); }
    protected function configure(): void {
        $this->setName('integration_weknora:poll-stage-status')->setDescription('Discover and compensate one bounded stage status queue pass');
    }
    protected function execute(InputInterface $input, OutputInterface $output): int {
        $this->statuses->pollDue(time()); return Command::SUCCESS;
    }
}
