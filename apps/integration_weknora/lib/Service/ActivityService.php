<?php

declare(strict_types=1);

namespace OCA\IntegrationWeknora\Service;

use OCP\Files\File;
use OCP\Files\IRootFolder;
use OCP\IConfig;
use OCP\IDBConnection;

/** User-safe lifecycle activity derived from authoritative local records. */
final class ActivityService {
    public function __construct(
        private IDBConnection $db,
        private IConfig $config,
        private IRootFolder $files,
        private DeletionTombstoneService $tombstones,
    ) {
    }

    /**
     * @return array{items:list<array<string,mixed>>,next_cursor:?string}
     */
    public function forScope(string $uid, string $bindingId, ?int $fileId,
        ?string $cursor = null, int $limit = 20): array {
        if (!preg_match('/\A[A-Za-z0-9_-]{1,128}\z/D', $bindingId) ||
            ($fileId !== null && $fileId < 1) || $limit < 1 || $limit > 100) {
            throw new \InvalidArgumentException('Invalid activity scope');
        }
        $offset = $cursor === null || $cursor === '' ? 0 :
            $this->decodeCursor($cursor, $uid, $bindingId, $fileId);
        $items = array_merge(
            $this->changeItems($uid, $bindingId, $fileId),
            $this->publicationItems($bindingId, $fileId),
            $fileId === null ? $this->directoryItems($bindingId) : [],
            $this->statusItems($bindingId, $fileId),
            $this->tombstoneItems($bindingId, $fileId),
        );
        usort($items, static fn(array $a, array $b): int =>
            ((int)$b['occurred_at'] <=> (int)$a['occurred_at']) ?: strcmp((string)$b['event_id'], (string)$a['event_id']));
        $unique = [];
        foreach ($items as $item) {
            $unique[$item['event_id']] ??= $item;
        }
        $items = array_values($unique);
        $slice = array_slice($items, $offset, $limit);
        $next = $offset + count($slice) < count($items)
            ? $this->encodeCursor($uid, $bindingId, $fileId, $offset + count($slice)) : null;
        return ['items' => $slice, 'next_cursor' => $next];
    }

    private function changeItems(string $uid, string $bindingId, ?int $fileId): array {
        $query = $this->db->getQueryBuilder();
        $query->select('id', 'file_id', 'event_type', 'old_path', 'path', 'created_at')
            ->from('weknora_outbox')
            ->where($query->expr()->eq('binding_id', $query->createNamedParameter($bindingId)));
        if ($fileId !== null) {
            $query->andWhere($query->expr()->eq('file_id', $query->createNamedParameter($fileId)));
        }
        $result = $query->orderBy('id', 'DESC')->setMaxResults(500)->executeQuery();
        try {
            $rows = $result->fetchAllAssociative();
        } finally {
            $result->closeCursor();
        }
        $out = [];
        foreach ($rows as $row) {
            $type = (string)$row['event_type'];
            $id = (int)$row['file_id'];
            $name = $this->visibleFileName($uid, $id);
            $label = $name ?? ($id > 0 ? '一个文件' : '发布范围');
            [$title, $result] = match ($type) {
                'upsert' => ['检测到文件更新', '等待当前版本验证'],
                'metadata' => ['文件名称或位置已更新', '内容版本保持不变'],
                'delete' => ['检测到文件删除', '知识访问已关闭，等待删除确认与回收核验'],
                'subtree_deleted' => ['检测到目录删除', '子树访问已关闭，等待完整清单确认'],
                'subtree_moved' => ['目录已移动', '正在重新评估发布范围'],
                'subtree_scan' => ['目录内容已变更', '正在重新扫描发布范围'],
                'reconcile' => ['正在完整核对发布范围', '事件水位不能单独证明文件可用'],
                default => ['发布范围已更新', '等待同步结果'],
            };
            $out[] = $this->item('change-' . $row['id'], (int)$row['created_at'],
                $type === 'delete' || $type === 'subtree_deleted' ? 'withdrawal' : 'sync',
                $title, $label, $result);
        }
        return $out;
    }

    private function publicationItems(string $bindingId, ?int $fileId): array {
        $query = $this->db->getQueryBuilder();
        $query->select('id', 'file_id', 'action', 'created_at')->from('weknora_pub_audit')
            ->where($query->expr()->eq('binding_id', $query->createNamedParameter($bindingId)));
        if ($fileId !== null) {
            $query->andWhere($query->expr()->eq('file_id', $query->createNamedParameter($fileId)));
        }
        $result = $query->orderBy('id', 'DESC')->setMaxResults(100)->executeQuery();
        try {
            $rows = $result->fetchAllAssociative();
        } finally {
            $result->closeCursor();
        }
        $out = [];
        foreach ($rows as $row) {
            $withdrawn = $row['action'] === 'withdrawn';
            $out[] = $this->item('publication-' . $row['id'], (int)$row['created_at'],
                $withdrawn ? 'withdrawal' : 'publication',
                $withdrawn ? '文件已从当前发布集合撤回' : '文件重新允许发布',
                '一个文件',
                $withdrawn ? '原文件保留，知识副本进入受控回收' : '将重新读取当前版本并验证权限');
        }
        return $out;
    }

    private function directoryItems(string $bindingId): array {
        $query = $this->db->getQueryBuilder();
        $query->select('id', 'action', 'reason_code', 'created_at')->from('weknora_directory_audit')
            ->where($query->expr()->eq('binding_id', $query->createNamedParameter($bindingId)))
            ->orderBy('id', 'DESC')->setMaxResults(100);
        $result = $query->executeQuery();
        try {
            $rows = $result->fetchAllAssociative();
        } finally {
            $result->closeCursor();
        }
        $labels = [
            'directory_created' => '目录已创建并进入关联流程',
            'association_requested' => '已请求关联知识库',
            'operation_retry' => '已重试同一目录操作',
            'sync_paused' => '内容同步已暂停',
            'sync_resumed' => '内容同步已恢复',
            'sync_now_requested' => '已请求立即同步',
        ];
        $out = [];
        foreach ($rows as $row) {
            $action = (string)$row['action'];
            $out[] = $this->item('directory-' . $row['id'], (int)$row['created_at'], 'directory',
                $labels[$action] ?? '目录配置已更新', '发布目录',
                $row['reason_code'] ? '原因码：' . $row['reason_code'] : '操作已记录');
        }
        return $out;
    }

    private function statusItems(string $bindingId, ?int $fileId): array {
        $query = $this->db->getQueryBuilder();
        $query->select('file_id', 'body_json', 'state_sha256', 'last_verified_at')
            ->from('weknora_file_status')
            ->where($query->expr()->eq('binding_id', $query->createNamedParameter($bindingId)));
        if ($fileId !== null) {
            $query->andWhere($query->expr()->eq('file_id', $query->createNamedParameter($fileId)));
        }
        $result = $query->orderBy('last_verified_at', 'DESC')->setMaxResults(100)->executeQuery();
        try {
            $rows = $result->fetchAllAssociative();
        } finally {
            $result->closeCursor();
        }
        $out = [];
        foreach ($rows as $row) {
            try {
                $state = StatusProtocol::decode((string)$row['body_json'], time());
            } catch (\Throwable) {
                continue;
            }
            $at = $state['occurred_at'] ?: (int)$row['last_verified_at'];
            $label = '一个文件';
            foreach ($state['stages'] as $stage => $stageState) {
                if ($stageState['completed_at'] === null) {
                    continue;
                }
                $stageLabel = ['parse' => '解析', 'chunk' => '分块',
                    'embedding' => '嵌入', 'index' => '索引'][$stage];
                $out[] = $this->item('stage-' . $row['state_sha256'] . '-' . $stage,
                    (int)$stageState['completed_at'], 'processing',
                    $stageLabel . ($stageState['state'] === 'succeeded' ? '完成' : '结束'),
                    $label, $stageState['state'] === 'succeeded' ? '处理成功' : '处理未成功');
            }
            $cleanup = (string)($state['gc_state'] ?? 'unverified');
            if (!in_array($cleanup, ['none', 'unverified'], true)) {
                $out[] = $this->item('cleanup-' . $row['state_sha256'], $at, 'cleanup',
                    '知识副本回收状态已更新', $label, match ($cleanup) {
                        'verified', 'completed', 'cleanup_verified' => '回收已验证',
                        'blocked', 'cleanup_blocked' => '回收受引用或租约阻断',
                        'partial', 'cleanup_partial' => '部分资源已回收，等待重试',
                        default => '回收进行中',
                    });
            }
        }
        return $out;
    }

    private function tombstoneItems(string $bindingId, ?int $fileId): array {
        $query = $this->db->getQueryBuilder();
        $query->select('file_id', 'reason_code', 'observed_at', 'generation', 'cleanup_state')
            ->from('weknora_delete_tomb')
            ->where($query->expr()->eq('binding_id', $query->createNamedParameter($bindingId)));
        if ($fileId !== null) {
            $query->andWhere($query->expr()->eq('file_id', $query->createNamedParameter($fileId)));
        }
        $result = $query->orderBy('observed_at', 'DESC')->setMaxResults(100)->executeQuery();
        try {
            $rows = $result->fetchAllAssociative();
        } finally {
            $result->closeCursor();
        }
        $out = [];
        foreach ($rows as $row) {
            $cleanup = match ((string)$row['cleanup_state']) {
                'verified' => '回收已验证',
                'pending' => '回收待开始',
                'blocked' => '回收受引用或租约阻断',
                'running' => '回收中',
                'partial' => '回收部分完成，等待重试',
                'withdrawn' => '知识已撤回，回收状态待验证',
                default => '回收状态待验证',
            };
            $out[] = $this->item('tombstone-' . $row['file_id'] . '-' . $row['generation'],
                (int)$row['observed_at'], 'withdrawal', '已确认源文件删除',
                '一个文件', '知识已撤回；' . $cleanup);
        }
        return $out;
    }

    private function visibleFileName(string $uid, ?int $fileId): ?string {
        if ($fileId === null || $fileId < 1) {
            return null;
        }
        try {
            foreach ($this->files->getUserFolder($uid)->getById($fileId) as $node) {
                if ($node instanceof File && $node->isReadable()) {
                    return $node->getName();
                }
            }
        } catch (\Throwable) {
        }
        return null;
    }

    private function item(string $id, int $at, string $kind, string $title,
        string $label, string $result): array {
        return ['event_id' => substr($id, 0, 128), 'occurred_at' => $at, 'kind' => $kind,
            'title' => $title, 'label' => $label, 'result' => $result,
            'request_id' => substr(hash('sha256', $id), 0, 12)];
    }

    private function encodeCursor(string $uid, string $bindingId, ?int $fileId, int $offset): string {
        $body = rtrim(strtr(base64_encode(json_encode([
            'uid' => $uid, 'binding' => $bindingId, 'file' => $fileId, 'offset' => $offset,
            'issued_at' => time(),
        ], JSON_THROW_ON_ERROR)), '+/', '-_'), '=');
        return $body . '.' . hash_hmac('sha256', $body, $this->cursorSecret());
    }

    private function decodeCursor(string $cursor, string $uid, string $bindingId, ?int $fileId): int {
        if (strlen($cursor) > 1024 ||
            !preg_match('/\A([A-Za-z0-9_-]+)\.([a-f0-9]{64})\z/D', $cursor, $parts) ||
            !hash_equals(hash_hmac('sha256', $parts[1], $this->cursorSecret()), $parts[2])) {
            throw new \InvalidArgumentException('Invalid activity cursor');
        }
        $data = json_decode((string)base64_decode(strtr($parts[1], '-_', '+/'), true),
            true, 8, JSON_THROW_ON_ERROR);
        if (!is_array($data) || ($data['uid'] ?? null) !== $uid ||
            ($data['binding'] ?? null) !== $bindingId || ($data['file'] ?? null) !== $fileId ||
            !is_int($data['offset'] ?? null) || $data['offset'] < 0 ||
            !is_int($data['issued_at'] ?? null) || $data['issued_at'] < time() - 300 ||
            $data['issued_at'] > time() + 5) {
            throw new \InvalidArgumentException('Invalid activity cursor');
        }
        return $data['offset'];
    }

    private function cursorSecret(): string {
        $secret = $this->config->getSystemValueString('secret');
        if (strlen($secret) < 16) {
            throw new \RuntimeException('Activity cursor key is unavailable');
        }
        return hash_hmac('sha256', 'nextcloud-weknora-activity-v1', $secret);
    }
}
