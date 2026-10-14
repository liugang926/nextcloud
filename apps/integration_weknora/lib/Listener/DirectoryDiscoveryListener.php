<?php
declare(strict_types=1);
namespace OCA\IntegrationWeknora\Listener;
use OCA\IntegrationWeknora\Service\DirectoryDiscoveryService;
use OCP\EventDispatcher\Event;
use OCP\EventDispatcher\IEventListener;
use OCP\Files\Events\Node\NodeCreatedEvent;
use OCP\Files\Events\Node\NodeCopiedEvent;
use OCP\Files\Events\Node\NodeRenamedEvent;
use OCP\Files\Folder;
use Psr\Log\LoggerInterface;
/** A successful file operation records only a DB hint; no HTTP or LDAP calls. */
final class DirectoryDiscoveryListener implements IEventListener {
    public function __construct(private DirectoryDiscoveryService $discovery, private LoggerInterface $logger) {}
    public function handle(Event $event): void {
        $node = match (true) {
            $event instanceof NodeCreatedEvent => $event->getNode(),
            $event instanceof NodeCopiedEvent, $event instanceof NodeRenamedEvent => $event->getTarget(),
            default => null,
        };
        if (!$node instanceof Folder) { return; }
        try { $this->discovery->enqueue($node->getId()); }
        catch (\Throwable $e) { $this->logger->error('Directory discovery hint could not be recorded', ['error_type' => get_class($e)]); }
    }
}
