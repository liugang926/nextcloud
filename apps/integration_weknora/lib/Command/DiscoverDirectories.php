<?php
declare(strict_types=1);
namespace OCA\IntegrationWeknora\Command;
use OCA\IntegrationWeknora\Service\DirectoryDiscoveryService;
use Symfony\Component\Console\Command\Command;
use Symfony\Component\Console\Input\InputInterface;
use Symfony\Component\Console\Output\OutputInterface;
final class DiscoverDirectories extends Command {
    public function __construct(private DirectoryDiscoveryService $discovery) { parent::__construct(); }
    protected function configure(): void {
        $this->setName('integration_weknora:discover-directories')->setDescription('Process one bounded directory discovery queue pass');
    }
    protected function execute(InputInterface $input, OutputInterface $output): int {
        $this->discovery->deliverDue(); return Command::SUCCESS;
    }
}
