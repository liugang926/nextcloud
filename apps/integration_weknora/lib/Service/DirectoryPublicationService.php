<?php
declare(strict_types=1);
namespace OCA\IntegrationWeknora\Service;

use OCP\Files\Folder;
use OCP\Files\IRootFolder;
use OCP\IConfig;
use OCP\IDBConnection;
use OCP\IGroupManager;

/** Session intents, discovery and retry converge on one physical publication root. */
final class DirectoryPublicationService {
    private const APP = 'integration_weknora';
    public function __construct(private IDBConnection $db, private IRootFolder $files,
        private IGroupManager $groups, private IConfig $config, private BindingRegistryService $bindings,
        private PublicationAclPolicyService $acl, private IdentityMappingService $identities,
        private SourcePairingRegistryService $pairs, private EventConnectionService $connections,
        private ChangeOutboxService $changes, private DirectoryOnboardingClient $remote,
        private DirectoryGrantSnapshotService $grants, private EmployeeFileStatusService $statuses,
        private RootMigrationService $rootMoves, private TargetMigrationService $targetMoves, private ControlRecoveryService $recovery) {}

    /** Observes existing authority; it never grants, chooses or resumes a manager. */
    public function authorizeCurrentManager(string $bindingId, array $scope): array {
        $scope = ManagerAuthorizationProtocol::request($bindingId, $scope);
        $before = $this->currentManagerMetadata($bindingId, $scope);
        $after = $this->currentManagerMetadata($bindingId, $scope);
        if ($before !== $after) { throw new \UnexpectedValueException('Manager authority changed'); }
        return ManagerAuthorizationProtocol::decision($bindingId, $scope, true, 'authorized') + [
            'root_file_id' => (string)$after['root_file_id'], 'rule_id' => $after['rule_id'],
            'rule_revision' => (string)$after['rule_revision'],
            'publication_epoch' => (string)$after['publication_epoch'],
            'policy_revision' => hash('sha256', json_encode($after, JSON_THROW_ON_ERROR)),
        ];
    }

    /** No folder name, request name, document or template body is projected. */
    private function currentManagerMetadata(string $bindingId, array $scope): array {
        $this->recovery->requireAutomationOpen($this->config);
        // Absence is unknown, not an implicit reopened recovery control.
        $gateQuery = $this->db->getQueryBuilder();
        $gateResult = $gateQuery->select('closed')->from('weknora_control_gate')
            ->where($gateQuery->expr()->eq('id', $gateQuery->createNamedParameter(1)))->executeQuery();
        try { $gate = $gateResult->fetchAssociative(); } finally { $gateResult->closeCursor(); }
        ManagerAuthorizationProtocol::requireRecoveryOpen($gate);

        if ($this->config->getSystemValueString('instanceid') !== $scope['instance_id']) {
            throw new \UnexpectedValueException('Manager instance is unavailable');
        }
        DirectoryTargetClosureService::requireOpen($this->db, $bindingId);
        $pair = $this->pairs->status($bindingId);
        if ($pair === null || $pair['state'] !== 'active' || $pair['binding_id'] !== $bindingId) {
            throw new \UnexpectedValueException('Manager pair is unavailable');
        }
        foreach (['instance_id', 'operation_id', 'tenant_id', 'knowledge_base_id', 'data_source_id'] as $key) {
            if (($pair[$key] ?? null) !== $scope[$key]) { throw new \UnexpectedValueException('Manager pair changed'); }
        }
        $binding = null;
        foreach ($this->bindings->listBindings() as $candidate) {
            if ($candidate['id'] === $bindingId) { $binding = $candidate; break; }
        }
        if ($binding === null) { throw new \UnexpectedValueException('Manager binding unavailable'); }
        $epoch = $this->bindings->requirePublicationActive($bindingId);
        $root = $this->bindings->requireActiveRoot($bindingId);
        $this->acl->assertTreeSupported($root);
        $intent = $this->managerIntentMetadata($scope['operation_id']);
        if ($intent === null || !in_array($intent['state'], ['pending', 'active'], true) || $intent['binding_id'] !== $bindingId ||
            (int)$intent['root_file_id'] !== $root->getId() ||
            $intent['root_key'] !== self::physicalKey($root) ||
            $intent['processing_template_id'] !== $scope['processing_template_id']) {
            throw new \UnexpectedValueException('Manager intent unavailable');
        }
        $rule = $this->rule($intent['rule_id']);
        $matching = $this->matchingRule($root);
        if ($matching === null || $matching['id'] !== $rule['id'] ||
            !is_array($rule['delegates'] ?? null) || !array_is_list($rule['delegates']) ||
            !is_int($rule['revision'] ?? null) || $rule['revision'] <= 0) {
            throw new \UnexpectedValueException('Manager rule unavailable');
        }
        $allowed = array_filter($this->targetChoices($rule), static fn(array $target): bool =>
            ($target['tenant_id'] ?? null) === $scope['tenant_id'] &&
            ($target['processing_template_id'] ?? null) === $scope['processing_template_id']);
        if (count($allowed) !== 1) { throw new CurrentManagerDeniedException('Current target policy revoked'); }
        DirectoryTenantDeletionService::requireTenantOpen($this->db, $rule['weknora_origin'], $scope['tenant_id']);
        $identity = $this->identities->resolve($scope['directory_id'], $scope['object_guid']);
        if ($identity === null) { throw new CurrentManagerDeniedException('Current identity denied'); }
        $admin = $this->groups->isAdmin($identity['uid']);
        if (!$admin && !in_array($identity['uid'], $rule['delegates'], true)) {
            throw new CurrentManagerDeniedException('Current publication delegate revoked');
        }
        // A publication delegate also needs its own current root view. This is
        // independent of, and cannot replace, per-file source READ authority.
        $view = $this->files->getUserFolder($identity['uid']);
        $readable = false;
        foreach ($view->getById($root->getId()) as $candidate) {
            if ($candidate instanceof Folder && $candidate->getId() === $root->getId() &&
                $view->isSubNode($candidate) && $candidate->isReadable()) { $readable = true; break; }
        }
        if (!$readable) { throw new CurrentManagerDeniedException('Current root READ denied'); }
        unset($rule['discovery_after_id']);
        return ['scope' => $scope, 'binding_id' => $bindingId, 'pair' => $pair,
            'root_file_id' => $root->getId(), 'root_hash' => SourcePairingRegistryService::rootHash($root),
            'rule_id' => $rule['id'], 'rule_revision' => $rule['revision'],
            'rule_sha256' => hash('sha256', json_encode($rule, JSON_THROW_ON_ERROR)),
            'mapping_id' => $identity['mapping_id'], 'uid' => $identity['uid'],
            'current_admin' => $admin, 'publication_epoch' => $epoch];
    }

    private function managerIntentMetadata(string $operationId): ?array {
        $q = $this->db->getQueryBuilder();
        $column = $q->getColumnName('request_json');
        $template = match ($this->config->getSystemValueString('dbtype')) {
            'pgsql' => "COALESCE($column::jsonb->>'processing_template_id','')",
            'mysql' => "COALESCE(JSON_UNQUOTE(JSON_EXTRACT($column,'$.processing_template_id')),'')",
            'sqlite', 'sqlite3' => "COALESCE(json_extract($column,'$.processing_template_id'),'')",
            default => throw new \UnexpectedValueException('Manager metadata backend unavailable'),
        };
        $q->select('operation_id', 'binding_id', 'root_key', 'root_file_id', 'rule_id', 'state')
            ->selectAlias($q->createFunction($template), 'processing_template_id')->from('weknora_dir_intent')
            ->where($q->expr()->eq('operation_id', $q->createNamedParameter($operationId)))->setMaxResults(2);
        $r = $q->executeQuery();
        try { $rows = $r->fetchAllAssociative(); } finally { $r->closeCursor(); }
        return count($rows) === 1 ? $rows[0] : null;
    }

    public static function physicalKey(Folder $folder): string {
        // Filecache IDs remain stable on a rename and are shared across user views.
        return hash('sha256', 'nextcloud-filecache-root-v1' . "\0" . $folder->getId());
    }

    public static function contains(Folder $root, Folder $folder): bool {
        if ($root->getId() === $folder->getId()) { return true; }
        if ($root->getStorage()->getId() === $folder->getStorage()->getId()) {
            $prefix = trim($root->getInternalPath(), '/');
            $path = trim($folder->getInternalPath(), '/');
            if ($prefix === '' || str_starts_with($path . '/', $prefix . '/')) { return true; }
        }
        // Shared mounts may wrap the storage differently. Walk the real user
        // view by filecache ID; path/name equality is never a source identity.
        $seen = [];
        for ($node = $folder; $node instanceof Folder && count($seen) < 256;) {
            $id = $node->getId();
            if ($id === $root->getId()) { return true; }
            if (isset($seen[$id])) { break; }
            $seen[$id] = true;
            try { $node = $node->getParent(); } catch (\Throwable $e) { break; }
        }
        return false;
    }

    public function rules(): array {
        $q = $this->db->getQueryBuilder();
        $r = $q->select('*')->from('weknora_dir_rules')->orderBy('rule_id')->executeQuery();
        try {
            $out = [];
            while (($row = $r->fetchAssociative()) !== false) {
                $rule = json_decode((string)$row['config_json'], true, 16, JSON_THROW_ON_ERROR);
                if (!is_array($rule)) { throw new \UnexpectedValueException('Invalid directory rule'); }
                $out[] = $rule + ['revision' => (int)$row['revision'], 'discovery_after_id' => (int)$row['discovery_after_id']];
            }
            return $out;
        } finally { $r->closeCursor(); }
    }

    public function saveRule(array $rule, int $expectedRevision): array {
        $this->recovery->requireAutomationOpen($this->config);
        foreach (['id', 'owner_uid', 'tenant_id', 'weknora_origin', 'base_url', 'processing_template_id'] as $field) {
            if (!is_string($rule[$field] ?? null) || $rule[$field] === '' || strlen($rule[$field]) > 512) {
                throw new \InvalidArgumentException('Invalid directory rule');
            }
        }
        if (!preg_match('/\A[A-Za-z0-9_-]{1,128}\z/D', $rule['id']) ||
            !ctype_digit($rule['tenant_id']) || filter_var($rule['tenant_id'], FILTER_VALIDATE_INT, ['options' => ['min_range' => 1]]) === false ||
            !is_int($rule['container_file_id'] ?? null) || $rule['container_file_id'] < 1 ||
            !in_array($rule['discovery'] ?? null, ['direct_children', 'explicit'], true) ||
            !is_array($rule['delegates'] ?? null) || !array_is_list($rule['delegates']) ||
            !is_bool($rule['auto_enabled'] ?? null) || $expectedRevision < 0) {
            throw new \InvalidArgumentException('Invalid directory rule');
        }
        foreach ($rule['delegates'] as $uid) {
            if (!is_string($uid) || $uid === '' || strlen($uid) > 64) {
                throw new \InvalidArgumentException('Invalid publication delegate');
            }
        }
        if ($rule['auto_enabled'] && ($rule['discovery'] !== 'direct_children' ||
            !is_string($rule['automation_uid'] ?? null) ||
            !in_array($rule['automation_uid'], $rule['delegates'], true))) {
            throw new \InvalidArgumentException('Automatic publication requires an explicit delegate');
        }
        $scheme = getenv('WEKNORA_EVENT_DEV_HTTP') === '1' ? 'https?' : 'https';
        foreach (['weknora_origin', 'base_url'] as $field) {
            if (!preg_match('~\A' . $scheme . '://[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?(?::[1-9][0-9]{0,4})?\z~D', $rule[$field])) {
                throw new \InvalidArgumentException('Canonical server origins are required');
            }
        }

        if (isset($rule['targets'])) {
            if (!is_array($rule['targets']) || !array_is_list($rule['targets']) || count($rule['targets']) < 1 || count($rule['targets']) > 32) { throw new \InvalidArgumentException('Invalid target spaces'); }
            $seenTargets = [];
            foreach ($rule['targets'] as &$target) {
                if (!is_array($target)) { throw new \InvalidArgumentException('Invalid target space'); }
                $spaceName = $target['space_name'] ?? '';
                if (!SourcePairingRegistryService::validTenantId($target['tenant_id'] ?? null) ||
                    !SourcePairingRegistryService::validRemoteId($target['processing_template_id'] ?? null) ||
                    !is_string($target['credential_id'] ?? null) || !preg_match('/\A[A-Za-z0-9_-]{1,128}\z/D', $target['credential_id']) ||
                    !is_string($spaceName) || strlen($spaceName) > 255 ||
                    isset($seenTargets[$target['tenant_id']."\0".$target['processing_template_id']])) {
                    throw new \InvalidArgumentException('Invalid or duplicate target space');
                }
                $seenTargets[$target['tenant_id']."\0".$target['processing_template_id']] = true;
                $target = array_intersect_key($target, array_flip(['tenant_id','processing_template_id','credential_id','space_name']));
            }
            unset($target);
            if (!isset($seenTargets[$rule['tenant_id']."\0".$rule['processing_template_id']])) { throw new \InvalidArgumentException('The automatic default target must be explicitly allowed'); }
        }
        // Validate egress without making a request or exposing the credential.
        (new EventReceiverPolicy())->requireApproved($rule['weknora_origin'] . EventReceiverPolicy::PATH);
        $container = $this->folder($rule['owner_uid'], $rule['container_file_id']);
        $this->acl->assertSupported($container);
        $allowed = ['id', 'owner_uid', 'container_file_id', 'tenant_id', 'weknora_origin', 'base_url',
            'processing_template_id', 'discovery', 'delegates', 'auto_enabled', 'automation_uid', 'targets'];
        $rule = array_intersect_key($rule, array_flip($allowed));
        $this->db->beginTransaction();
        try {
            $this->lock();
            $this->recovery->requireAutomationOpen($this->config);
            $old = null;
            foreach ($this->rules() as $other) {
                if ($other['id'] === $rule['id']) { $old = $other; continue; }
                $otherContainer = $this->folder($other['owner_uid'], $other['container_file_id']);
                if (self::contains($container, $otherContainer) || self::contains($otherContainer, $container)) {
                    throw new \DomainException('Publication rules overlap');
                }
            }
            if (($old['revision'] ?? 0) !== $expectedRevision) { throw new \DomainException('Stale rule revision'); }
            if ($old !== null) {
                foreach (['owner_uid', 'container_file_id', 'tenant_id', 'processing_template_id', 'weknora_origin', 'base_url'] as $field) {
                    if ($old[$field] !== $rule[$field] && $this->hasRuleIntents($rule['id'])) {
                        throw new \DomainException('Existing sources require a durable migration');
                    }
                }
            }

            if ($old !== null && $this->hasRuleIntents($rule['id'])) {
                foreach ($old['targets'] ?? [] as $oldTarget) {
                    $preserved = false;
                    foreach ($rule['targets'] ?? [] as $target) { if ($target === $oldTarget) { $preserved = true; break; } }
                    if (!$preserved) { throw new \DomainException('Existing target scopes require a durable migration'); }
                }
            }
            $revision = $expectedRevision + 1;
            $q = $this->db->getQueryBuilder();
            $json = json_encode($rule, JSON_THROW_ON_ERROR);
            if ($old === null) {
                $q->insert('weknora_dir_rules')->values(['rule_id' => $q->createNamedParameter($rule['id']),
                    'config_json' => $q->createNamedParameter($json), 'revision' => $q->createNamedParameter($revision),
                    'updated_at' => $q->createNamedParameter(time())])->executeStatement();
            } else {
                $q->update('weknora_dir_rules')->set('config_json', $q->createNamedParameter($json))
                    ->set('revision', $q->createNamedParameter($revision))->set('updated_at', $q->createNamedParameter(time()))
                    ->where($q->expr()->eq('rule_id', $q->createNamedParameter($rule['id'])))->executeStatement();
            }
            $this->db->commit();
            return $rule + ['revision' => $revision];
        } catch (\Throwable $e) { $this->db->rollBack(); throw $e; }
    }

    public function show(string $uid, int $folderId): array {
        $folder = $this->folder($uid, $folderId);
        foreach ($this->bindings->listBindings() as $binding) {
            $root = $this->folder($binding['owner_uid'], $binding['root_file_id']);
            if (!self::contains($root, $folder)) { continue; }
            if (!$this->statuses->summaryAuthority($uid, $binding['id'], false)) { continue; }
            $intent = $this->find('binding_id', $binding['id']);
            $rule = $intent === null ? null : $this->rule((string)$intent['rule_id']);
            $pair = $this->pairs->status($binding['id']);
            $closed = DirectoryTargetClosureService::lookup($this->db, 'binding_id', $binding['id']);
            $operation = $intent === null ? null : $this->publicIntent($intent);
            if ($closed !== null) { $operation = ['operation_id' => $closed['target']['operation_id'], 'binding_id' => $binding['id'],
                'folder_id' => $binding['root_file_id'], 'state' => 'target_deleted', 'error_code' => 'target_deleted', 'revision' => (int)($intent['revision'] ?? 0)]; }
            $spaceName = null;
            if ($rule !== null && $pair !== null) {
                foreach ($rule['targets'] ?? [] as $target) {
                    if (is_array($target) && (string)($target['tenant_id'] ?? '') === (string)($pair['tenant_id'] ?? '') &&
                        is_string($target['space_name'] ?? null)) {
                        $spaceName = $target['space_name'];
                        break;
                    }
                }
            }
            return ['folder_id' => $folderId, 'source_state' => $closed === null ? 'bound' : 'target_deleted', 'binding_id' => $binding['id'],
                'binding_name' => $binding['name'], 'inherited' => $folderId !== $binding['root_file_id'],
                'manageable' => $folderId === $binding['root_file_id'] && $rule !== null && $this->delegated($uid, $rule),
                'publication_state' => $binding['publication_state'], 'sync_enabled' => $binding['sync_enabled'],
                'sync_revision' => $binding['sync_revision'], 'pairing_state' => $pair['state'] ?? 'pending',
                'tenant_id' => $closed['target']['tenant_id'] ?? $pair['tenant_id'] ?? null, 'knowledge_base_id' => $closed['target']['knowledge_base_id'] ?? $pair['knowledge_base_id'] ?? null,
                'space_name' => $spaceName,
                'operation' => $operation];
        }
        $intent = $this->find('root_key', self::physicalKey($folder));
        $closed = DirectoryTargetClosureService::lookup($this->db, 'root_key', self::physicalKey($folder));
        if ($closed !== null) {
            return ['folder_id' => $folderId, 'source_state' => 'target_deleted', 'inherited' => false, 'manageable' => false, 'can_create' => false,
                'binding_id' => $closed['target']['binding_id'], 'tenant_id' => $closed['target']['tenant_id'], 'knowledge_base_id' => $closed['target']['knowledge_base_id'],
                'publication_state' => 'stopped', 'sync_enabled' => false, 'operation' => ['operation_id' => $closed['target']['operation_id'],
                    'state' => 'target_deleted', 'error_code' => 'target_deleted', 'revision' => (int)($intent['revision'] ?? 0)]];
        }
        $rule = $this->matchingRule($folder);
        $creationRule = $this->matchingRule($folder, true);
        return ['folder_id' => $folderId, 'source_state' => $intent === null ? 'unbound' : 'pending',
            'inherited' => false, 'manageable' => $rule !== null && $this->delegated($uid, $rule),
            'can_create' => $creationRule !== null && $this->delegated($uid, $creationRule),
            'operation' => $intent === null ? null : $this->publicIntent($intent)];
    }

    public function candidates(
        string $uid,
        int $folderId,
        bool $forCreation = false,
        ?array $allowedTenantIds = null,
    ): array {
        $folder = $this->folder($uid, $folderId);
        $rule = $forCreation ? $this->matchingRule($folder, true) : $this->requireManagement($uid, $folder);
        if ($rule === null || !$this->delegated($uid, $rule)) { throw new \DomainException('Publication management is forbidden'); }
        $allowed = $allowedTenantIds === null ? null : array_fill_keys($allowedTenantIds, true);
        $identity = $this->identities->forUser($uid);
        $targets = [];
        foreach ($this->targetChoices($rule) as $target) {
            if ($allowed !== null && !isset($allowed[(string)$target['tenant_id']])) {
                continue;
            }
            try {
                $effective = $this->selectTarget($rule, $target['tenant_id'],$target['processing_template_id']);
                $response = $this->remote->request($effective, 'GET', '/candidates', [
                    'tenant_id' => $effective['tenant_id'],'processing_template_id'=>$effective['processing_template_id'], 'actor_directory_id' => $identity['directory_id'],
                    'actor_object_guid' => $identity['object_guid']]);
                if((string)($response['tenant_id']??'')!==$effective['tenant_id']||($response['processing_template_id']??null)!==$effective['processing_template_id']){throw new \DomainException('Scoped candidate template identity mismatch');}
                $targets[] = ['tenant_id' => $effective['tenant_id'], 'processing_template_id' => $effective['processing_template_id'],
                    'space_name' => is_string($effective['space_name'] ?? null) ? $effective['space_name'] : null,
                    'template_name'=>is_string($response['processing_template_name']??null)?$response['processing_template_name']:$effective['processing_template_id'],
                    'graph_mode'=>in_array($response['graph_mode']??null,['off','optional','required'],true)?$response['graph_mode']:null,
                    'candidates' => $response['candidates'] ?? []];
            } catch (\DomainException $e) { /* Do not expose an unauthorized space or KB. */ }
        }
        $first = $targets[0] ?? ['tenant_id' => null, 'processing_template_id' => null, 'candidates' => []];
        return ['rule_id' => $rule['id'], 'targets' => $targets] + $first;
    }

    /** The intent commits before newFolder(), including the exact user's target choice. */
    public function create(string $uid, int $parentId, string $name, array $choice): array {
        $this->recovery->requireAutomationOpen($this->config);
        if ($name === '' || strlen($name) > 255 || in_array($name, ['.', '..'], true) ||
            preg_match('/[\/\\\\\x00-\x1f\x7f]/', $name)) {
            throw new \InvalidArgumentException('Invalid directory name');
        }
        $parent = $this->folder($uid, $parentId);
        if (!$parent->isCreatable()) { throw new \DomainException('Directory creation is forbidden'); }
        $rule = $this->matchingRule($parent, true);
        if ($rule === null || !$this->delegated($uid, $rule)) { throw new \DomainException('Publication management is forbidden'); }
        $identity = $this->identities->forUser($uid);
        $intent = $this->reserve($rule, $uid, $identity, $choice, 0, $parentId, $name,
            hash('sha256', 'new-directory' . "\0" . $parentId . "\0" . $name));
        if ((int)$intent['root_file_id'] === 0) {
            // A crash after mkdir is repaired against this persisted name and
            // parent. A pre-existing folder is rejected before intent creation.
            $folder = $parent->nodeExists($name) ? $parent->get($name) : $parent->newFolder($name);
            if (!$folder instanceof Folder) { throw new \DomainException('Created path is not a directory'); }
            $this->attachRoot($intent, $folder);
        }
        return $this->retry($uid, (string)$intent['operation_id']);
    }

    public function associate(string $uid, int $folderId, array $choice, bool $process = true): array {
        $this->recovery->requireAutomationOpen($this->config);
        $folder = $this->folder($uid, $folderId);
        $rule = $this->requireManagement($uid, $folder);
        $identity = $this->identities->forUser($uid);
        $intent = $this->reserve($rule, $uid, $identity, $choice, $folderId, 0, $folder->getName(), self::physicalKey($folder));
        if (!$process) { return $this->publicIntent($intent); }
        return $this->retry($uid, (string)$intent['operation_id']);
    }

    public function retry(string $uid, string $operationId): array {
        $this->recovery->requireAutomationOpen($this->config);
        $intent = $this->find('operation_id', $operationId);
        if ($intent === null) { throw new \OutOfBoundsException('Directory operation is unavailable'); }
        if (in_array($intent['state'], ['retired', 'target_deleted'], true) || DirectoryTargetClosureService::lookup($this->db, 'operation_id', $operationId) !== null) { throw new \DomainException('Source target is closed'); }
        $rule = $this->rule((string)$intent['rule_id']);
        if (!$this->delegated($uid, $rule)) { throw new \DomainException('Publication management is forbidden'); }
        if ((int)$intent['root_file_id'] > 0) { $this->folder($uid, (int)$intent['root_file_id']); }

        $this->rootMoves->retryUnchanged((string)$intent['binding_id'], $this->identities->forUser($uid));
        $this->process($operationId, true, $uid);
        return $this->publicIntent($this->find('operation_id', $operationId) ?? $intent);
    }


    public function switchTarget(string $uid, int $folderId, array $choice): array {
        $this->recovery->requireAutomationOpen($this->config);
        $root=$this->folder($uid,$folderId);$rule=$this->requireManagement($uid,$root);
        if(SourcePairingRegistryService::validOperationId($choice['operation_id']??null)&&($known=$this->targetMoves->existingChoice($choice['operation_id'],$folderId))!==null){
            foreach(['mode','knowledge_base_id','name'] as $field){if(isset($choice[$field])&&$choice[$field]!==$known[$field]){throw new \DomainException('Target migration choice changed');}}
            foreach(['tenant_id','processing_template_id'] as $field){if(isset($choice[$field])&&(string)$choice[$field]!== (string)$known[$field]){throw new \DomainException('Target migration scope changed');}}
            return $this->targetMigration($uid,$folderId,$choice['operation_id'],true);
        }
        $intent=$this->find('root_key',self::physicalKey($root));
        if($intent===null||in_array($intent['state'],['retired','target_deleted'],true)||DirectoryTargetClosureService::lookup($this->db,'operation_id',$intent['operation_id'])!==null||(int)$intent['root_file_id']!==$folderId){throw new \DomainException('An existing root source is required');}
        $target=$this->selectTarget($rule,is_string($choice['tenant_id']??null)?$choice['tenant_id']:null,is_string($choice['processing_template_id']??null)?$choice['processing_template_id']:null);
        if(!SourcePairingRegistryService::validOperationId($choice['operation_id']??null)||!in_array($choice['mode']??null,['create','existing'],true)||($choice['mode']==='existing'&&!SourcePairingRegistryService::validRemoteId($choice['knowledge_base_id']??null))){throw new \InvalidArgumentException('Invalid migration target');}
        $identity=$this->identities->forUser($uid);
        $request=['operation_id'=>'','tenant_id'=>(int)$target['tenant_id'],'instance_id'=>$this->config->getSystemValueString('instanceid'),'binding_id'=>'',
            'actor_directory_id'=>$identity['directory_id'],'actor_object_guid'=>$identity['object_guid'],'mode'=>$choice['mode'],'name'=>$choice['name']??$root->getName(),
            'knowledge_base_id'=>$choice['mode']==='existing'?$choice['knowledge_base_id']:'','processing_template_id'=>$target['processing_template_id']];
        $oldRule=$this->rule($intent['rule_id']);if(!$this->delegated($uid,$oldRule)){throw new \DomainException('Original source management is forbidden');}
        return $this->targetMoves->begin($intent,$request,$rule,$uid,$choice['operation_id'],$oldRule);
    }

    public function targetMigration(string $uid,int $folderId,string $operationId,bool $retry=false): array {
        if ($retry) { $this->recovery->requireAutomationOpen($this->config); }
        $root=$this->folder($uid,$folderId);$this->requireManagement($uid,$root);
        $oldRule=$this->rule($this->targetMoves->originalRule($operationId,$folderId));if(!$this->delegated($uid,$oldRule)){throw new \DomainException('Original source management is forbidden');}
        if(!$retry){return $this->targetMoves->status($operationId,$folderId);}
        $identity=$this->identities->forUser($uid);
        return $this->targetMoves->retry($operationId,$folderId,['actor_directory_id'=>$identity['directory_id'],'actor_object_guid'=>$identity['object_guid']],$uid);
    }

    public function sync(string $uid, int $folderId, bool $enabled, int $revision): array {
        $this->recovery->requireAutomationOpen($this->config);
        $status = $this->show($uid, $folderId);
        if (!($status['manageable'] ?? false) || !is_array($status['operation'] ?? null)) {
            throw new \DomainException('Publication management is forbidden');
        }
        $intent = $this->find('operation_id', $status['operation']['operation_id']);

        DirectoryTargetClosureService::requireOpen($this->db, $intent['binding_id']);
        $rule = $this->rule($intent['rule_id']);
        $request = json_decode($intent['request_json'], true, 16, JSON_THROW_ON_ERROR);
        $rule = $this->selectTarget($rule, (string)$request['tenant_id'],(string)$request['processing_template_id']);
        $identity = $this->identities->forUser($uid);
        // The target performs the authority check and CAS before local update.
        $remote = $this->remote->request($rule, 'POST', '/operations/' . $intent['operation_id'] . '/sync',
            ['sync_enabled' => $enabled, 'expected_revision' => $revision,
                'actor_directory_id' => $identity['directory_id'], 'actor_object_guid' => $identity['object_guid']]);
        $next = $remote['revision'] ?? $remote['operation']['revision'] ?? null;
        if (!is_int($next) || $next < $revision) { throw new \RuntimeException('Sync result is unverified'); }
        $q = $this->db->getQueryBuilder();
        $q->update('weknora_binding_id')->set('sync_enabled', $q->createNamedParameter($enabled ? 1 : 0))
            ->set('sync_revision', $q->createNamedParameter($next))
            ->where($q->expr()->eq('binding_id', $q->createNamedParameter($intent['binding_id'])))
            ->andWhere($q->expr()->lte('sync_revision', $q->createNamedParameter($next)))->executeStatement();
        return $this->show($uid, $folderId);
    }

    /** Bounded cron compensation; uploads never perform remote HTTP in node events. */
    public function reconcile(): void {
        $this->recovery->requireAutomationOpen($this->config);
        $this->rootMoves->reconcile();
        foreach ($this->rules() as $rule) {
            if (!$rule['auto_enabled']) { continue; }
            $container = $this->folder($rule['owner_uid'], $rule['container_file_id']);
            $nodes = $container->getDirectoryListing();
            usort($nodes, static fn ($a, $b): int => $a->getId() <=> $b->getId());
            $count = 0; $last = 0; $more = false;
            foreach ($nodes as $node) {
                if (!$node instanceof Folder || $node->getId() <= $rule['discovery_after_id']) { continue; }
                if (++$count > 100) { $more = true; break; }
                $last = $node->getId();
                if ($this->find('root_key', self::physicalKey($node)) !== null || DirectoryTargetClosureService::lookup($this->db, 'root_key', self::physicalKey($node)) !== null) { continue; }
                if ($this->pendingCreation($container->getId(), $node->getName())) { continue; }
                try {
                    $uid = $rule['automation_uid'];
                    $identity = $this->identities->forUser($uid);
                    $this->reserve($rule, $uid, $identity, ['operation_id' => self::uuid(), 'mode' => 'create',
                        'name' => $node->getName()], $node->getId(), 0, $node->getName(), self::physicalKey($node));
                } catch (\DomainException $e) { /* Conflict keeps originals and never creates a target. */ }
            }
            $q = $this->db->getQueryBuilder();
            $q->update('weknora_dir_rules')->set('discovery_after_id', $q->createNamedParameter($more ? $last : 0))
                ->where($q->expr()->eq('rule_id', $q->createNamedParameter($rule['id'])))->executeStatement();
        }
        $this->processDue();
        $this->targetMoves->reconcile();
    }

    /** Fast worker pass: process durable intents without rescanning directory trees. */
    public function processDue(int $limit = 20): void {
        $this->recovery->requireAutomationOpen($this->config);
        $deadline = microtime(true) + 8;
        $q = $this->db->getQueryBuilder();
        $r = $q->select('i.operation_id')->from('weknora_dir_intent', 'i')
            ->leftJoin('i', 'weknora_target_close', 'closed', $q->expr()->eq('closed.operation_id', 'i.operation_id'))
            ->where($q->expr()->isNull('closed.operation_id'))
            ->andWhere($q->expr()->lte('i.next_attempt_at', $q->createNamedParameter(time())))
            ->andWhere($q->expr()->lte('i.lease_until', $q->createNamedParameter(time())))
            ->andWhere($q->expr()->in('i.state', $q->createNamedParameter(['pending', 'active'], \OCP\DB\QueryBuilder\IQueryBuilder::PARAM_STR_ARRAY)))
            ->orderBy('i.updated_at')->setMaxResults(max(1, min(100, $limit)))->executeQuery();
        try { $rows = $r->fetchAllAssociative(); } finally { $r->closeCursor(); }
        // Independent control metadata gets a bounded opportunity on every
        // pass, including after the original root has permanently closed.
        // It never calls process(), pairing, grants, mkdir, sync or resume.
        $this->observeTenantDeletionMetadataDue(min(3, max(1, $limit)), $deadline);
        foreach ($rows as $row) {
            if (microtime(true) >= $deadline) { break; }
            $this->process($row['operation_id']);
        }
    }

    private function reserve(array $rule, string $uid, array $identity, array $choice, int $rootId,
        int $parentId, string $name, string $rootKey): array {
        if (!SourcePairingRegistryService::validOperationId($choice['operation_id'] ?? null) ||
            !in_array($choice['mode'] ?? null, ['create', 'existing'], true) ||
            ($choice['mode'] === 'existing' && !SourcePairingRegistryService::validRemoteId($choice['knowledge_base_id'] ?? null))) {
            throw new \InvalidArgumentException('Invalid target choice');
        }

        $rule = $this->selectTarget($rule, is_string($choice['tenant_id'] ?? null) ? $choice['tenant_id'] : null,is_string($choice['processing_template_id']??null)?$choice['processing_template_id']:null);
        if (isset($choice['processing_template_id']) && $choice['processing_template_id'] !== $rule['processing_template_id']) { throw new \DomainException('Target processing template changed'); }
        $operation = strtolower($choice['operation_id']);
        $binding = 'dir_' . str_replace('-', '', $operation);
        $request = ['operation_id' => $operation, 'tenant_id' => (int)$rule['tenant_id'],
            'instance_id' => $this->config->getSystemValueString('instanceid'), 'binding_id' => $binding,
            'actor_directory_id' => $identity['directory_id'], 'actor_object_guid' => $identity['object_guid'],
            'mode' => $choice['mode'], 'name' => $choice['name'] ?? $name,
            'knowledge_base_id' => $choice['mode'] === 'existing' ? $choice['knowledge_base_id'] : '',
            'processing_template_id' => $rule['processing_template_id']];
        if (!is_string($request['name']) || $request['name'] === '' || strlen($request['name']) > 255) {
            throw new \InvalidArgumentException('Invalid knowledge base name');
        }
        $this->db->beginTransaction();
        try {
            $this->lock();
            $this->recovery->requireAutomationOpen($this->config);
            $existing = $this->find('operation_id', $operation);
            if ($existing !== null) {
                if ($existing['actor_uid'] !== $uid || $existing['rule_id'] !== $rule['id'] ||
                    (int)$existing['parent_file_id'] !== $parentId ||
                    json_decode($existing['request_json'], true, 16, JSON_THROW_ON_ERROR) !== $request ||
                    ($rootId > 0 && (int)$existing['root_file_id'] !== $rootId)) {
                    throw new \DomainException('Directory operation conflicts with its original intent');
                }
                if (in_array($existing['state'], ['retired', 'target_deleted'], true) || DirectoryTargetClosureService::lookup($this->db, 'operation_id', $operation) !== null) { throw new \DomainException('The original source target is closed'); }
                $this->db->commit(); return $existing;
            }
            if (DirectoryTargetClosureService::lookup($this->db, 'root_key', $rootKey) !== null || DirectoryTargetClosureService::lookup($this->db, 'operation_id', $operation) !== null) { throw new \DomainException('The original directory target was deleted'); }
            if ($this->find('root_key', $rootKey) !== null) { throw new \DomainException('Directory already has a publication intent'); }
            if ($rootId === 0 && $this->folder($uid, $parentId)->nodeExists($name)) {
                throw new \DomainException('Directory already exists; associate it from its details');
            }
            if ($rootId > 0) { $this->assertNoOverlap($this->folder($rule['owner_uid'], $rootId)); }
            $now = time();
            $row = ['operation_id' => $operation, 'binding_id' => $binding, 'root_key' => $rootKey,
                'rule_id' => $rule['id'], 'owner_uid' => $rule['owner_uid'], 'actor_uid' => $uid,
                'folder_name' => $name, 'root_file_id' => $rootId, 'parent_file_id' => $parentId,
                'request_json' => json_encode($request, JSON_THROW_ON_ERROR), 'state' => 'pending', 'error_code' => '',
                'revision' => 1, 'permission_version' => 0, 'attempt_count' => 0, 'next_attempt_at' => 0,
                'lease_until' => 0, 'created_at' => $now, 'updated_at' => $now];
            if ($this->db->insertIgnoreConflict('weknora_dir_intent', $row) !== 1) {
                throw new \DomainException('Directory is already reserved');
            }
            $this->db->commit(); return $row;
        } catch (\Throwable $e) { $this->db->rollBack(); throw $e; }
    }

    private function process(string $operationId, bool $allowResume = false, ?string $managerUid = null): void {
        $this->recovery->requireAutomationOpen($this->config);
        if (DirectoryTargetClosureService::lookup($this->db, 'operation_id', $operationId) !== null) { return; }
        $q = $this->db->getQueryBuilder();
        if ($q->update('weknora_dir_intent')->set('lease_until', $q->createNamedParameter(time() + 600))
            ->where($q->expr()->eq('operation_id', $q->createNamedParameter($operationId)))
            ->andWhere($q->expr()->in('state', $q->createNamedParameter(['pending', 'active'], \OCP\DB\QueryBuilder\IQueryBuilder::PARAM_STR_ARRAY)))
            ->andWhere($q->expr()->lte('lease_until', $q->createNamedParameter(time())))->executeStatement() !== 1) { return; }
        $intent = $this->find('operation_id', $operationId);
        $stage = 'intent';
        try {
            DirectoryTargetClosureService::requireOpen($this->db, $intent['binding_id']);
            $rule = $this->rule($intent['rule_id']);
            $managerUid = $managerUid ?? $this->targetMoves->managerForOperation($operationId);
            $uid = $managerUid ?? $intent['actor_uid'];
            if (!$this->delegated($uid, $rule)) { throw new \DomainException('Publication delegate was revoked'); }
            $identity = $this->identities->forUser($uid);
            $request = json_decode($intent['request_json'], true, 16, JSON_THROW_ON_ERROR);
            $rule = $this->selectTarget($rule, (string)$request['tenant_id'],(string)$request['processing_template_id']);
            if ($request['processing_template_id'] !== $rule['processing_template_id']) { throw new \DomainException('Target processing template changed'); }
            if ($managerUid === null && ($request['actor_directory_id'] !== $identity['directory_id'] ||
                $request['actor_object_guid'] !== $identity['object_guid'])) {
                throw new \DomainException('Directory actor identity changed');
            }
            if ((int)$intent['root_file_id'] === 0) {
                $parent = $this->folder($uid, (int)$intent['parent_file_id']);
                // Only a recorded create operation owns this mkdir repair.
                $node = $parent->nodeExists($intent['folder_name']) ? $parent->get($intent['folder_name']) : $parent->newFolder($intent['folder_name']);
                if (!$node instanceof Folder) { throw new \DomainException('Directory creation conflicts'); }
                $this->attachRoot($intent, $node);
                $intent = $this->find('operation_id', $operationId);
            }
            $root = $this->folder($rule['owner_uid'], (int)$intent['root_file_id']);
            $currentRule = $this->matchingRule($root);
            if ($currentRule === null || $currentRule['id'] !== $rule['id']) { throw new \DomainException('Root left its publication rule'); }
            $this->folder($uid, $root->getId());
            $this->acl->assertTreeSupported($root);
            $this->assertNoOverlap($root, $intent['binding_id'], $operationId);
            $existingPair = $this->pairs->status($intent['binding_id']);
            $currentActor = ['actor_directory_id' => $identity['directory_id'], 'actor_object_guid' => $identity['object_guid']];
            $instance = $this->config->getSystemValueString('instanceid');
            $floor = 0;
            foreach ($this->bindings->listBindings() as $binding) {
                if ($binding['id'] === $intent['binding_id']) { $floor = $binding['sync_revision']; break; }
            }
            $readStatus = null;
            $readStatus = function() use ($rule, $operationId, $currentActor, $intent, $request, &$stage): array {
                $stage = 'target_status';
                $response = $this->remote->request($rule, 'GET', '/operations/' . $operationId, $currentActor);
                $this->retainTenantDeletionObservation($response, $intent, $request, $rule);
                return $response;
            };
            if ($existingPair !== null ||
                ($managerUid !== null && ($request['actor_directory_id'] !== $identity['directory_id'] || $request['actor_object_guid'] !== $identity['object_guid']))) {
                $remote = $readStatus();
            } else {
                $stage = 'target_reservation';
                try {
                    $remote = $this->remote->request($rule, 'POST', '/operations', $request);
                } catch (\Throwable $reservationFailure) {
                    // A lost reserve ACK may leave a real original operation
                    // whose target was subsequently deleted before pairing.
                    // The failed POST grants nothing: only the same scoped,
                    // current-actor authenticated GET 200 can close that intent.
                    $target = TargetDeletionProtocol::deletedAfterFailedReservation($reservationFailure,
                        $readStatus, $intent, $request, $existingPair, $instance, $floor);
                    $this->closeTarget($intent, $target);
                    return;
                }
            }
            $operation = $remote['operation'] ?? null;
            if (!is_array($operation) || ($operation['operation_id'] ?? null) !== $operationId ||
                !SourcePairingRegistryService::validRemoteId($operation['knowledge_base_id'] ?? null)) {
                throw new \RuntimeException('Target reservation is unverified');
            }
            // POST reserve/pair responses cannot establish target deletion.
            // Obtain a fresh successful status through the approved credential,
            // TLS/actor/recovery path before any local resume or remote grants.
            $observed = $readStatus();
            $target = TargetDeletionProtocol::observation($observed, $intent, $request, $existingPair, $instance, $floor);
            if ($target['knowledge_base_id'] !== $operation['knowledge_base_id']) { throw new \UnexpectedValueException('Reserved target identity changed'); }
            if ($target['state'] === 'target_deleted') {
                $this->closeTarget($intent, $target);
                return;
            }
            $stage = 'binding';
            $this->bindings->save($intent['binding_id'], $root->getName(), $rule['owner_uid'], $root->getId());
            if ($allowResume && ($oldPair=$this->pairs->status($intent['binding_id']))!==null && $oldPair['state']==='active') {
                // Current ACL/tree, identity, rule and target delegation above
                // are fresh. Resume preserves the source and KB identities.
                $this->bindings->setPublicationState($intent['binding_id'], 'active', $uid);
            }
            $stage = 'pairing_prepare';
            $prepared = $this->pairs->prepare($intent['binding_id'], $operationId, $rule['tenant_id'], $operation['knowledge_base_id'], $uid);
            $pair = $prepared['pairing'];
            $stage = 'pairing_remote';
            $received = $this->remote->request($rule, 'POST', '/operations/' . $operationId . '/pair', [
                'base_url' => $rule['base_url'], 'binding_id' => $intent['binding_id'], 'operation_id' => $operationId,
                'knowledge_base_id' => $operation['knowledge_base_id'],
                'instance_id' => $request['instance_id'], 'publication_epoch' => $pair['publication_epoch'],
                'key_id' => $pair['key_id'], 'token' => $this->pairs->handoffToken($intent['binding_id'], $operationId),
                'actor_directory_id' => $identity['directory_id'], 'actor_object_guid' => $identity['object_guid']]);
            $stage = 'pairing_ack';
            $pairNow = $this->pairs->status($intent['binding_id']);
            if ($pairNow === null || $pairNow['state'] !== 'active' || $pairNow['operation_id'] !== $operationId) {
                throw new \RuntimeException('Signed pairing acknowledgement is pending');
            }
            $stage = 'event_connection';
            $event = $received['event_connection'] ?? $received['operation']['event_connection'] ?? null;
            if (!is_array($event)) { throw new \RuntimeException('Independent event connection is pending'); }
            if (($event['receiver_url'] ?? null) === EventReceiverPolicy::PATH) {
                $event['receiver_url'] = $rule['weknora_origin'] . EventReceiverPolicy::PATH;
            }
            $existing = $this->connections->row($intent['binding_id']);
            if ($existing === null) {
                $this->connections->configure($intent['binding_id'], $intent['binding_id'], $request['instance_id'],
                    $event['connection_id'], $event['key_id'], $event['secret'], $event['receiver_url']);
            } elseif ($existing['connection_id'] !== $event['connection_id'] || $existing['key_id'] !== $event['key_id']) {
                throw new \DomainException('Event connection changed');
            }
            $permissionVersion = (int)$intent['permission_version'] + 1;
            $stage = 'grant_snapshot';
            $snapshot = $this->grants->snapshot($root, $rule['owner_uid'], $uid);
            $stage = 'grant_remote';
            $this->remote->request($rule, 'PUT', '/operations/' . $operationId . '/grants', [
                'permission_version' => $permissionVersion, 'publication_epoch' => $this->bindings->requirePublicationActive($intent['binding_id']),
                'complete' => true, 'subjects' => $snapshot]);
            $stage = 'reconcile';
            $this->changes->append($intent['binding_id'], null, 'reconcile');
            $remoteRevision = $received['operation']['revision'] ?? null;
            if (is_int($remoteRevision) && $remoteRevision >= 1) {
                $q = $this->db->getQueryBuilder();
                $q->update('weknora_binding_id')->set('sync_revision', $q->createNamedParameter($remoteRevision))
                    ->where($q->expr()->eq('binding_id', $q->createNamedParameter($intent['binding_id'])))
                    ->andWhere($q->expr()->lte('sync_revision', $q->createNamedParameter($remoteRevision)))->executeStatement();
            }
            $stage = 'finalize';
            $this->update($operationId, ['state' => 'active', 'error_code' => '', 'permission_version' => $permissionVersion,
                'next_attempt_at' => time() + 60, 'lease_until' => 0]);
        } catch (\Throwable $e) {
            // If a previously active root becomes unverifiable, close its
            // publication gate before any grant reconciliation can fail.
            if ($intent['state'] === 'active') {
                try { $this->bindings->setPublicationState($intent['binding_id'], 'stopped', 'directory-policy'); } catch (\Throwable $ignored) {}
            }
            $remoteObservation = null;
            if ($stage === 'pairing_remote' && is_callable($readStatus)) {
                try {
                    $remoteObservation = $readStatus();
                } catch (\Throwable $ignored) {
                    // Keep the original failure and never persist remote error text.
                }
            }
            $this->update($operationId, ['state' => 'pending',
                'error_code' => self::failureCode($stage, $e, $remoteObservation),
                'attempt_count' => (int)$intent['attempt_count'] + 1, 'lease_until' => 0,
                'next_attempt_at' => time() + min(300, 15 * (1 << min(4, (int)$intent['attempt_count'])))]);
        }
    }

    /**
     * Keep the queue status actionable without persisting remote error text or
     * credentials. Domain responses retain their existing conflict projection.
     */
    private static function failureCode(string $stage, \Throwable $error, ?array $remoteObservation = null): string {
        if ($error instanceof UnsupportedPublicationAclException) {
            return 'unsupported_source_acl';
        }
        if ($stage === 'target_status' && $error instanceof DirectoryOnboardingRemoteRejectedException) {
            return self::targetStatusRejectionCode($error);
        }
        if ($stage === 'target_status' && $error instanceof DirectoryOnboardingRequestException) {
            $remoteCode = $error->remoteCode() ?? '';
            $suffix = match (true) {
                str_starts_with($remoteCode, 'onboarding_unavailable_') =>
                    '_onboarding_' . substr($remoteCode, strlen('onboarding_unavailable_')),
                $remoteCode === 'onboarding_unavailable' => '_onboarding',
                default => '',
            };
            if ($remoteCode === 'onboarding_unavailable' && $error->remoteStatus() !== null) {
                $suffix .= '_http_' . $error->remoteStatus();
            }
            return 'target_status_' . $error->reason() . $suffix;
        }
        if ($stage === 'pairing_remote' && $error instanceof DirectoryOnboardingRemoteRejectedException) {
            return self::pairingRemoteRejectionCode($error);
        }
        if ($error instanceof \DomainException) {
            return match ($stage) {
                'pairing_prepare' => 'pairing_prepare_conflict',
                'pairing_remote' => 'pairing_remote_rejected',
                'pairing_ack' => 'pairing_ack_conflict',
                'event_connection' => 'event_connection_conflict',
                default => 'permission_or_target_conflict',
            };
        }
        return match ($stage) {
            'target_reservation' => 'target_reservation_unverified',
            'target_status' => 'target_status_unverified',
            'pairing_prepare' => 'pairing_prepare_unverified',
            'pairing_remote' => self::pairingRemoteFailureCode($error, $remoteObservation),
            'pairing_ack' => 'pairing_ack_unverified',
            'event_connection' => 'event_connection_pending',
            'grant_snapshot' => 'grant_snapshot_unavailable',
            'grant_remote' => 'grant_sync_unverified',
            'reconcile' => 'reconcile_pending',
            default => 'connection_unverified',
        };
    }

    private static function pairingRemoteRejectionCode(DirectoryOnboardingRemoteRejectedException $error): string {
        $suffix = match ($error->remoteCode()) {
            'nextcloud_prepared_binding_unavailable' => 'binding_unavailable',
            'source_pairing_conflict' => 'source_conflict',
            'source_pairing_not_found' => 'source_missing',
            'nextcloud_onboarding_conflict' => 'onboarding_conflict',
            'nextcloud_target_closed' => 'target_closed',
            'nextcloud_onboarding_denied' => 'authorization_denied',
            'nextcloud_onboarding_not_found' => 'operation_missing',
            default => 'http_' . $error->getCode(),
        };
        return 'pairing_remote_rejected_' . $suffix;
    }

    private static function targetStatusRejectionCode(DirectoryOnboardingRemoteRejectedException $error): string {
        $remoteCode = $error->remoteCode();
        $failureStage = '';
        foreach (['operation_read', 'tenant_deletion', 'target_observation', 'operation_refresh'] as $stage) {
            $stageSuffix = '_' . $stage;
            if (is_string($remoteCode) && str_ends_with($remoteCode, $stageSuffix)) {
                $remoteCode = substr($remoteCode, 0, -strlen($stageSuffix));
                $failureStage = '_' . $stage;
                break;
            }
        }
        $suffix = match ($remoteCode) {
            'nextcloud_onboarding_conflict' => 'onboarding_conflict',
            'nextcloud_onboarding_denied' => 'authorization_denied',
            'nextcloud_onboarding_not_found' => 'operation_missing',
            'nextcloud_target_closed' => 'target_closed',
            default => 'http_' . $error->getCode(),
        };
        return 'target_status_rejected_' . $suffix . $failureStage;
    }

    private static function pairingRemoteFailureCode(\Throwable $error, ?array $remoteObservation = null): string {
        if (!$error instanceof DirectoryOnboardingRequestException) {
            return 'pairing_remote_unverified';
        }
        $suffix = match ($error->remoteCode()) {
            'source_pairing_unavailable' => '_source_prepare',
            'onboarding_unavailable' => '_onboarding',
            default => '',
        };
        $code = 'pairing_remote_' . $error->reason() . $suffix;
        if (!in_array($error->reason(), [
            DirectoryOnboardingRequestException::SERVER_ERROR,
            DirectoryOnboardingRequestException::INVALID_RESPONSE,
        ], true)) {
            return $code;
        }
        if ($remoteObservation === null) {
            return $code;
        }
        $operation = is_array($remoteObservation['operation'] ?? null) ? $remoteObservation['operation'] : [];
        $pair = is_array($remoteObservation['pairing'] ?? null) ? $remoteObservation['pairing'] : [];
        $operationState = self::safeRemoteState($operation['state'] ?? null);
        $pairState = self::safeRemoteState($pair['state'] ?? null);
        if ($operationState === null || $pairState === null) {
            return $code;
        }
        return $code . '_op_' . $operationState . '_pair_' . $pairState;
    }

    private static function safeRemoteState(mixed $value): ?string {
        if ($value === null) {
            return 'missing';
        }
        return is_string($value) && in_array($value, [
            'reserved', 'pending', 'active', 'aborted', 'retired', 'target_deleted',
        ], true) ? $value : null;
    }

    /** Fresh metadata only; an immutable old root fact is never reopened. */
    private function observeTenantDeletionMetadataDue(int $limit, float $deadline): void {
        $q = $this->db->getQueryBuilder();
        // Expressions must be typed functions: raw strings are quoted as
        // column identifiers by Nextcloud's query builder.
        $nextCheck = $q->createFunction('COALESCE(' . $q->getColumnName('next_check_at', 'p') . ', 0)');
        $leaseUntil = $q->createFunction('COALESCE(' . $q->getColumnName('lease_until', 'p') . ', 0)');
        $r = $q->select('i.operation_id')->from('weknora_dir_intent', 'i')
            ->leftJoin('i', 'weknora_deletion_probe', 'p', $q->expr()->eq('i.operation_id', 'p.operation_id'))
            ->where($q->expr()->in('i.state', $q->createNamedParameter(['target_deleted', 'retired'], \OCP\DB\QueryBuilder\IQueryBuilder::PARAM_STR_ARRAY)))
            ->andWhere($q->expr()->lte($nextCheck, $q->createNamedParameter(time())))
            ->andWhere($q->expr()->lte($leaseUntil, $q->createNamedParameter(time())))
            ->orderBy($nextCheck)->addOrderBy('i.operation_id')->setMaxResults($limit)->executeQuery();
        try { $rows = $r->fetchAllAssociative(); } finally { $r->closeCursor(); }
        foreach ($rows as $row) {
            if (microtime(true) >= $deadline) { break; }
            $operationId = $row['operation_id'];
            $this->db->insertIgnoreConflict('weknora_deletion_probe', ['operation_id' => $operationId, 'next_check_at' => 0, 'lease_until' => 0]);
            $u = $this->db->getQueryBuilder();
            if ($u->update('weknora_deletion_probe')->set('lease_until', $u->createNamedParameter(time() + 60))
                ->where($u->expr()->eq('operation_id', $u->createNamedParameter($operationId)))
                ->andWhere($u->expr()->lte('next_check_at', $u->createNamedParameter(time())))
                ->andWhere($u->expr()->lte('lease_until', $u->createNamedParameter(time())))->executeStatement() !== 1) { continue; }
            $next = time() + 300;
            try {
                $this->recovery->requireAutomationOpen($this->config);
                $intent = $this->find('operation_id', $operationId);
                if ($intent === null || !in_array($intent['state'], ['target_deleted', 'retired'], true)) { throw new \DomainException('Metadata operation changed'); }
                $rule = $this->rule($intent['rule_id']); $request = json_decode($intent['request_json'], true, 16, JSON_THROW_ON_ERROR);
                $rule = $this->selectTarget($rule, (string)$request['tenant_id'], (string)$request['processing_template_id'], true);
                if (DirectoryTenantDeletionService::lookup($this->db, TenantDeletionProtocol::originHash($rule['weknora_origin']), (string)$request['tenant_id']) !== null) { $next = PHP_INT_MAX; continue; }
                $uid = $this->targetMoves->managerForOperation($operationId) ?? $intent['actor_uid'];
                if (!$this->delegated($uid, $rule)) { throw new \DomainException('Metadata delegate revoked'); }
                $identity = $this->identities->forUser($uid);
                $response = $this->remote->request($rule, 'GET', '/operations/' . $operationId,
                    ['actor_directory_id' => $identity['directory_id'], 'actor_object_guid' => $identity['object_guid']]);
                if ($this->retainTenantDeletionObservation($response, $intent, $request, $rule) !== null) { $next = PHP_INT_MAX; }
            } catch (\Throwable $unverified) {
                // Missing actor/credential/GET200/typed fact changes no root,
                // pair, grants, terminal state, credentials or material.
            } finally {
                $u = $this->db->getQueryBuilder(); $u->update('weknora_deletion_probe')->set('next_check_at', $u->createNamedParameter($next))
                    ->set('lease_until', $u->createNamedParameter(0))->where($u->expr()->eq('operation_id', $u->createNamedParameter($operationId)))->executeStatement();
            }
        }
    }

    private function retainTenantDeletionObservation(array $response, array $intent, array $request, array $rule): ?array {
        $fact = TenantDeletionProtocol::observation($response, $intent, $request, $this->config->getSystemValueString('instanceid'), $rule['weknora_origin']);
        if ($fact === null) { return null; }
        $this->db->beginTransaction();
        try {
            $this->lock(); $this->recovery->requireAutomationOpen($this->config);
            $current = $this->find('operation_id', $intent['operation_id']);
            if ($current === null || $current['binding_id'] !== $intent['binding_id'] || $current['request_json'] !== $intent['request_json'] ||
                (int)$current['revision'] !== (int)$intent['revision'] || $current['rule_id'] !== $intent['rule_id']) { throw new \DomainException('Tenant deletion observation intent changed'); }
            DirectoryTenantDeletionService::validateLocalAnchor($this->db, $fact);
            $created = DirectoryTenantDeletionService::retainInTransaction($this->db, $fact);
            DirectoryTenantDeletionService::stopNamespaceInTransaction($this->db, $fact, $this->bindings);
            if ($created) { PublicationRecoveryLedger::appendInTransaction($this->db, $fact['tenant']['binding_id'], null, 'tenant_deleted', 0, $fact); }
            $this->db->commit(); return $fact;
        } catch (\Throwable $e) { $this->db->rollBack(); throw $e; }
    }

    /** A receipt closes only the original target; source retirement remains separate. */
    private function closeTarget(array $intent, array $target): void {
        $this->db->beginTransaction();
        try {
            $this->lock();
            $this->recovery->requireAutomationOpen($this->config);
            $current = $this->find('operation_id', $intent['operation_id']);
            if ($current === null || (int)$current['revision'] !== (int)$intent['revision'] || $current['request_json'] !== $intent['request_json'] ||
                $current['binding_id'] !== $intent['binding_id'] || $current['root_key'] !== $intent['root_key'] ||
                !in_array($current['state'], ['pending', 'active'], true)) { throw new \DomainException('Target closure intent changed'); }
            $request = json_decode($current['request_json'], true, 16, JSON_THROW_ON_ERROR);
            $pair = $this->pairs->status($current['binding_id']);
            if ($pair !== null) {
                foreach (['operation_id', 'instance_id', 'binding_id', 'tenant_id', 'knowledge_base_id'] as $field) {
                    if ((string)($pair[$field] ?? '') !== $target[$field]) { throw new \DomainException('Target closure pair changed'); }
                }
            }
            foreach ($this->bindings->listBindings() as $binding) {
                if ($binding['id'] === $current['binding_id'] && $binding['sync_revision'] > TargetDeletionProtocol::decimal($target['operation_revision'])) {
                    throw new \DomainException('Target closure control revision regressed');
                }
            }
            $fact = TargetDeletionProtocol::fact(['target' => $target, 'root_file_id' => (string)$current['root_file_id'], 'root_key' => $current['root_key']]);
            $created = DirectoryTargetClosureService::retainInTransaction($this->db, $fact);
            $this->bindings->setPublicationState($current['binding_id'], 'stopped', 'target-deleted');
            DirectoryTargetClosureService::closeRestoredIntentInTransaction($this->db, $fact);
            if ($created) { PublicationRecoveryLedger::appendInTransaction($this->db, $current['binding_id'], null, 'target_deleted', TargetDeletionProtocol::decimal($target['operation_revision']), $fact); }
            $this->db->commit();
        } catch (\Throwable $e) { $this->db->rollBack(); throw $e; }
    }

    private function attachRoot(array $intent, Folder $root): void {
        $this->db->beginTransaction();
        try {
            $this->lock();
            $this->recovery->requireAutomationOpen($this->config);
            $this->assertNoOverlap($root, null, $intent['operation_id']);
            if (($other = $this->find('root_key', self::physicalKey($root))) !== null && $other['operation_id'] !== $intent['operation_id']) {
                throw new \DomainException('Physical root is already reserved');
            }
            $this->update($intent['operation_id'], ['root_file_id' => $root->getId(), 'root_key' => self::physicalKey($root)]);
            $this->db->commit();
        } catch (\Throwable $e) { $this->db->rollBack(); throw $e; }
    }

    private function assertNoOverlap(Folder $root, ?string $bindingId = null, ?string $operationId = null): void {
        foreach ($this->bindings->listBindings() as $binding) {
            if ($binding['id'] === $bindingId) { continue; }
            $other = $this->folder($binding['owner_uid'], $binding['root_file_id']);
            if (self::contains($root, $other) || self::contains($other, $root)) { throw new \DomainException('Publication roots overlap'); }
        }
        $q = $this->db->getQueryBuilder();
        $r = $q->select('operation_id', 'owner_uid', 'root_file_id')->from('weknora_dir_intent')
            ->where($q->expr()->neq('state', $q->createNamedParameter('retired')))->executeQuery();
        try { $rows = $r->fetchAllAssociative(); } finally { $r->closeCursor(); }
        foreach ($rows as $row) {
            if ($row['operation_id'] === $operationId || (int)$row['root_file_id'] === 0) { continue; }
            $other = $this->folder($row['owner_uid'], (int)$row['root_file_id']);
            if (self::contains($root, $other) || self::contains($other, $root)) { throw new \DomainException('Publication intents overlap'); }
        }
    }

    private function requireManagement(string $uid, Folder $folder): array {
        $rule = $this->matchingRule($folder);
        if ($rule === null || !$this->delegated($uid, $rule)) { throw new \DomainException('Publication management is forbidden'); }
        if (!$folder->isCreatable() &&
            (($folder->getPermissions() & \OCP\Constants::PERMISSION_UPDATE) === 0)) {
            throw new \DomainException('Publication management is forbidden');
        }
        $this->acl->assertTreeSupported($this->folder($rule['owner_uid'], $folder->getId()));
        return $rule;
    }
    private function delegated(string $uid, array $rule): bool {
        return $this->groups->isAdmin($uid) || in_array($uid, $rule['delegates'], true);
    }
    private function matchingRule(Folder $folder, bool $creationParent = false): ?array {
        $match = null;
        foreach ($this->rules() as $rule) {
            $container = $this->folder($rule['owner_uid'], $rule['container_file_id']);
            $eligible = $creationParent && $container->getId() === $folder->getId();
            if ($creationParent && $rule['discovery']==='direct_children' && $container->getId()!==$folder->getId()) { continue; }
            if (!$eligible && self::contains($container, $folder) && $container->getId() !== $folder->getId()) {
                $eligible = $rule['discovery'] === 'explicit' || $folder->getParent()->getId() === $container->getId();
            }
            if (!$eligible) { continue; }
            if ($match !== null) { throw new \DomainException('Physical root has conflicting publication rules'); }
            $match = $rule;
        }
        return $match;
    }

    private function targetChoices(array $rule): array {
        return $rule['targets'] ?? [['tenant_id' => $rule['tenant_id'], 'processing_template_id' => $rule['processing_template_id']]];
    }
    private function selectTarget(array $rule, ?string $tenantId,?string $templateId=null, bool $metadataOnly = false): array {
        $useDefault=$tenantId===null&&$templateId===null;$tenantId=$tenantId??$rule['tenant_id'];
        if($useDefault){$templateId=$rule['processing_template_id'];}
        $matches=[];foreach($this->targetChoices($rule) as $target){if($target['tenant_id']===$tenantId&&($templateId===null||$target['processing_template_id']===$templateId)){$matches[]=$target;}}
        if(count($matches)!==1){throw new \DomainException('Target tenant/template combination is unavailable or ambiguous');}
        $selected = array_replace($rule,$matches[0]);
        if (!$metadataOnly) {
            try { DirectoryTenantDeletionService::requireTenantOpen($this->db, $selected['weknora_origin'], $selected['tenant_id']); }
            catch (BindingPublicationStoppedException $closed) { throw new \DomainException('Publication target Tenant is closed'); }
        }
        return $selected;
    }
    private function rule(string $id): array {
        foreach ($this->rules() as $rule) { if ($rule['id'] === $id) { return $rule; } }
        throw new \DomainException('Publication rule is unavailable');
    }
    private function folder(string $uid, int $id): Folder {
        if ($id < 1) { throw new \OutOfBoundsException('Directory is unavailable'); }
        foreach ($this->files->getUserFolder($uid)->getById($id) as $node) {
            if ($node instanceof Folder && $node->getId() === $id && $node->isReadable()) { return $node; }
        }
        throw new \OutOfBoundsException('Directory is unavailable');
    }
    private function hasRuleIntents(string $id): bool {
        return $this->find('rule_id', $id) !== null;
    }
    private function pendingCreation(int $parentId, string $name): bool {
        $q = $this->db->getQueryBuilder();
        $r = $q->select('operation_id')->from('weknora_dir_intent')
            ->where($q->expr()->eq('parent_file_id', $q->createNamedParameter($parentId)))
            ->andWhere($q->expr()->eq('folder_name', $q->createNamedParameter($name)))
            ->andWhere($q->expr()->eq('root_file_id', $q->createNamedParameter(0)))->setMaxResults(1)->executeQuery();
        try { return $r->fetchOne() !== false; } finally { $r->closeCursor(); }
    }
    private function find(string $column, string $value): ?array {
        if (!in_array($column, ['operation_id', 'binding_id', 'root_key', 'rule_id'], true)) { throw new \LogicException('Invalid intent lookup'); }
        $q = $this->db->getQueryBuilder();
        $r = $q->select('*')->from('weknora_dir_intent')->where($q->expr()->eq($column, $q->createNamedParameter($value)))->setMaxResults(1)->executeQuery();
        try { $row = $r->fetchAssociative(); return $row === false ? null : $row; } finally { $r->closeCursor(); }
    }
    private function update(string $id, array $fields): void {
        $q = $this->db->getQueryBuilder();
        $q->update('weknora_dir_intent')->where($q->expr()->eq('operation_id', $q->createNamedParameter($id)))
            ->andWhere($q->expr()->notIn('state', $q->createNamedParameter(['retired', 'target_deleted'], \OCP\DB\QueryBuilder\IQueryBuilder::PARAM_STR_ARRAY)));
        foreach ($fields + ['updated_at' => time()] as $column => $value) { $q->set($column, $q->createNamedParameter($value)); }
        $q->executeStatement();
    }
    private function lock(): void {
        $this->db->insertIgnoreConflict('weknora_bind_lock', ['id' => 1]);
        $q = $this->db->getQueryBuilder();
        $r = $q->select('id')->from('weknora_bind_lock')->where($q->expr()->eq('id', $q->createNamedParameter(1)))->forUpdate()->executeQuery();
        try { if ($r->fetchOne() === false) { throw new \RuntimeException('Directory registry lock is unavailable'); } } finally { $r->closeCursor(); }
    }
    private function publicIntent(array $row): array {
        if (DirectoryTargetClosureService::lookup($this->db, 'operation_id', $row['operation_id']) !== null) { $row['state'] = 'target_deleted'; $row['error_code'] = 'target_deleted'; }
        return ['operation_id' => $row['operation_id'], 'binding_id' => $row['binding_id'],
            'folder_id' => (int)$row['root_file_id'], 'state' => $row['state'], 'revision' => (int)$row['revision'],
            'error_code' => $row['error_code'], 'updated_at' => (int)$row['updated_at']];
    }
    public static function uuid(): string {
        $bytes = random_bytes(16); $bytes[6] = chr((ord($bytes[6]) & 15) | 64); $bytes[8] = chr((ord($bytes[8]) & 63) | 128);
        $hex = bin2hex($bytes);
        return substr($hex, 0, 8) . '-' . substr($hex, 8, 4) . '-' . substr($hex, 12, 4) . '-' . substr($hex, 16, 4) . '-' . substr($hex, 20);
    }
}
