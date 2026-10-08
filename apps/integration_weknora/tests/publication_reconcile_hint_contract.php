<?php

declare(strict_types=1);

// Run the real controller with small protocol fakes; no Nextcloud install or
// database is needed to check the post-commit failure and retry contract.
namespace OCP {
    interface IRequest {}
    interface IUserSession { public function getUser(): ?object; }
    interface IGroupManager { public function isAdmin(string $uid): bool; }
}

namespace OCP\AppFramework {
    class Controller {
        public function __construct(string $appName, \OCP\IRequest $request) {}
    }
}

namespace OCP\AppFramework\Http {
    class JSONResponse {
        public function __construct(
            private array $data,
            private int $status = 200,
            private array $headers = [],
        ) {}

        public function getData(): array { return $this->data; }
        public function getStatus(): int { return $this->status; }
    }
}

namespace OCP\Files {
    class File {
        public function __construct(private int $id) {}
        public function getId(): int { return $this->id; }
        public function isReadable(): bool { return true; }
    }

    class Folder {
        /** @param array<int, object> $children */
        public function __construct(private int $id, private array $children = []) {}
        public function getId(): int { return $this->id; }
        public function getById(int $id): array {
            return isset($this->children[$id]) ? [$this->children[$id]] : [];
        }
        public function isSubNode(object $node): bool {
            return isset($this->children[$node->getId()]);
        }
    }

    interface IRootFolder { public function getUserFolder(string $uid): Folder; }
}

namespace Psr\Log {
    interface LoggerInterface {
        public function error(string $message, array $context = []): void;
    }
}

namespace OCA\IntegrationWeknora\Service {
    class BindingRegistryService {
        public function listBindings(): array {
            return [['id' => 'test-binding', 'owner_uid' => 'admin', 'root_file_id' => 2]];
        }
    }

    class FilePublicationStateService {
        public string $state = 'eligible';
        public int $writes = 0;

        public function hasRecordedState(string $bindingId, int $fileId): bool { return $this->writes > 0; }
        public function getState(string $bindingId, int $fileId): string { return $this->state; }
        public function getDecision(string $bindingId, int $fileId): array {
            return ['state' => $this->state, 'decision_audit_id' => $this->writes];
        }
        public function withdraw(string $bindingId, int $fileId, string $actorUid): void {
            $this->state = 'withdrawn';
            $this->writes++;
        }
        public function republish(string $bindingId, int $fileId, string $actorUid): void {
            $this->state = 'eligible';
            $this->writes++;
        }
    }

    class ChangeOutboxService {
        public bool $failNext = false;
        public array $attempts = [];
        public array $recorded = [];

        public function __construct(private FilePublicationStateService $states) {}

        public function append(string $bindingId, ?int $fileId, string $type): int {
            $event = [$bindingId, $fileId, $type, $this->states->state];
            $this->attempts[] = $event;
            if ($this->failNext) {
                $this->failNext = false;
                throw new \RuntimeException('synthetic outbox failure');
            }
            $this->recorded[] = $event;
            return count($this->recorded);
        }
    }
}

namespace {
    require_once __DIR__ . '/../lib/Controller/PublicationController.php';

    use OCA\IntegrationWeknora\Controller\PublicationController;
    use OCA\IntegrationWeknora\Service\BindingRegistryService;
    use OCA\IntegrationWeknora\Service\ChangeOutboxService;
    use OCA\IntegrationWeknora\Service\FilePublicationStateService;
    use OCP\Files\File;
    use OCP\Files\Folder;
    use OCP\Files\IRootFolder;
    use OCP\IGroupManager;
    use OCP\IRequest;
    use OCP\IUserSession;
    use Psr\Log\LoggerInterface;

    function expect(bool $condition, string $description): void {
        if (!$condition) {
            throw new \RuntimeException($description);
        }
    }

    $states = new FilePublicationStateService();
    $outbox = new ChangeOutboxService($states);
    $file = new File(77);
    $bindingRoot = new Folder(2, [77 => $file]);
    $userFolder = new Folder(1, [2 => $bindingRoot, 77 => $file]);
    $root = new class($userFolder) implements IRootFolder {
        public function __construct(private Folder $userFolder) {}
        public function getUserFolder(string $uid): Folder { return $this->userFolder; }
    };
    $session = new class implements IUserSession {
        public function getUser(): ?object {
            return new class { public function getUID(): string { return 'admin'; } };
        }
    };
    $groups = new class implements IGroupManager {
        public function isAdmin(string $uid): bool { return $uid === 'admin'; }
    };
    $logger = new class implements LoggerInterface {
        public bool $failNext = false;
        public array $errors = [];
        public function error(string $message, array $context = []): void {
            $this->errors[] = [$message, $context];
            if ($this->failNext) {
                $this->failNext = false;
                throw new \RuntimeException('synthetic logger failure');
            }
        }
    };
    $controller = new PublicationController(
        new class implements IRequest {},
        new BindingRegistryService(),
        $root,
        $session,
        $groups,
        $states,
        $outbox,
        $logger,
    );

    $state = $controller->state('test-binding', 77);
    expect($state->getStatus() === 200 && $state->getData()['state'] === 'eligible' &&
        $state->getData()['decision_audit_id'] === 0,
        'read-only state failed');
    expect(!array_key_exists('reconcile_hint_recorded', $state->getData()) && !$outbox->attempts,
        'read-only state emitted a hint');

    $outbox->failNext = true;
    $withdraw = $controller->withdraw('test-binding', 77);
    expect($withdraw->getStatus() === 200 && $withdraw->getData()['state'] === 'withdrawn' &&
        $withdraw->getData()['excluded'] === true &&
        $withdraw->getData()['decision_audit_id'] === 1 &&
        $withdraw->getData()['reconcile_hint_recorded'] === false,
        'post-commit hint failure hid the committed withdrawal');
    expect($states->writes === 1 && count($outbox->attempts) === 1 && !$outbox->recorded,
        'withdrawal did not try one post-commit hint');
    expect(count($logger->errors) === 1 &&
        $logger->errors[0][1]['error_type'] === \RuntimeException::class,
        'hint failure was not observable');

    $outbox->failNext = true;
    $logger->failNext = true;
    $doubleFailure = $controller->withdraw('test-binding', 77);
    expect($doubleFailure->getStatus() === 200 &&
        $doubleFailure->getData()['state'] === 'withdrawn' &&
        $doubleFailure->getData()['excluded'] === true &&
        $doubleFailure->getData()['reconcile_hint_recorded'] === false,
        'logger failure hid the committed withdrawal after hint failure');
    expect($states->writes === 2 && count($outbox->attempts) === 2 && !$outbox->recorded,
        'double failure unexpectedly rolled back or recorded a hint');

    $retry = $controller->withdraw('test-binding', 77);
    expect($retry->getStatus() === 200 && $retry->getData()['state'] === 'withdrawn' &&
        $retry->getData()['reconcile_hint_recorded'] === true,
        'repeated withdrawal did not repair the hint');
    expect($states->writes === 3 && $outbox->recorded === [
        ['test-binding', 77, 'reconcile', 'withdrawn'],
    ], 'retry did not append a file-scoped reconcile hint after state write');

    $republish = $controller->republish('test-binding', 77);
    expect($republish->getStatus() === 200 && $republish->getData()['state'] === 'eligible' &&
        $republish->getData()['excluded'] === false &&
        $republish->getData()['reconcile_hint_recorded'] === true,
        'republish did not report the restored publication');
    expect($outbox->recorded === [
        ['test-binding', 77, 'reconcile', 'withdrawn'],
        ['test-binding', 77, 'reconcile', 'eligible'],
    ], 'republish did not append a file-scoped reconcile hint after state write');

    echo "publication reconciliation hint contract passed\n";
}
