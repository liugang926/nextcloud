# Nextcloud 0.6.0 → 0.8.0 兼容升级与控制初始化

本候选补齐 WeKnora 对接所需的最小前置链：永久目标删除控制及当前管理者授权。它不是已经完成的生产升级或双端联动验收。实际 PostgreSQL 升级、恢复及完整配对授权仍须以当前候选的独立测试证据为准；不得把旧版本的成功记录作为本候选的验收。

## 兼容范围

- 插件版本从 0.6.0 增至 0.8.0，合并 0.7.0 的连接管理界面与 0.8.0 的 current-manager 授权/永久删除控制；Nextcloud 主版本仍为 34。
- Migration `Version0029Date20261014000000` 增加 `weknora_target_close`、`weknora_tenant_close`、`weknora_deletion_probe`，并为 `weknora_recovery_log` 增加 `tenant_namespace`。数据库表名前缀由 Nextcloud 决定。
- 保留目标和 Tenant 的永久删除事实；已关闭目标不会由旧 worker、迟到成功或恢复旧数据库重新打开。恢复 journal 必须携带这些事实。
- 新增 `POST /apps/integration_weknora/api/v1/bindings/{binding}/authorize-manager`，使用现有配对服务令牌认证精确的请求体，并重新核对实例、operation、Tenant、KB、datasource、模板、当前 AD 身份、管理授权和根目录 READ。该决定不代替逐文件 source READ。
- 此链对应 WeKnora 的永久删除协议和当前管理者授权能力，不能只摘授权路由而省略 Migration 29、删除事实及恢复约束。

不在此候选范围内的事项包括配置生产 LDAP、创建真实业务配对、替用户选择 Tenant/模板、授予管理者、恢复已删除目标、自动打开来源，以及生产 HTTPS 入口调整。

## 升级前置与边界

以下是供已授权运维人员复核后执行的操作框架；示例变量必须换成已验证的实际值。先在任务独占的 Nextcloud/PostgreSQL 环境验证，再进入生产变更。禁止在共享实例上运行测试 fixture。

1. 固定并记录旧版与候选代码提交、插件版本、数据库版本、容器 ID、镜像身份和 Compose 项目归属。不得用相同的 0.6.0 字符串推断协议已经兼容。
2. 停止双方普通业务入口、cron、事件/状态/目录 worker 和其他写入者。保持独立的恢复通道；不要只停止一个 HTTP 容器而留下后台写入。
3. 保存匹配的代码、数据库、配置和文件检查点，验证实际恢复工具与应用数据库角色权限。外部 journal、密钥及独立 pin 必须位于检查点、还原卷和源码目录之外，不能随数据库一起回滚。
4. 使用完整的既有 journal 和原密钥，从 sequence 0 连续覆盖所需历史到当前 head。不得为绕过缺页、旧 stream、错误 HMAC 或已知永久删除事实而新建 journal/密钥。刚启用 journal 不会补造此前的历史覆盖；未知历史需要另外核对双方权威现状。
5. 部署时让真实 Nextcloud 升级流程运行 Migration 29；不得手工改 `installed_version` 跳过迁移。数据库升级失败时保持维护和入口关闭。

升级后至少核对：安装版本为 0.8.0、Migration 29 已记录、上述表/列实际存在，旧配置及配对元数据保留，永久删除事实未丢失，manager 元数据查询在实际 PostgreSQL 上执行成功。插件升级不代表 gate 已初始化。

## gate 的语义

Migration 28 只创建 `weknora_control_gate` 表，没有初始化开放行，普通应用启动也不负责生成开放权限。manager 授权明确拒绝缺失、未知或关闭的 gate；不能将“无行”解释为新安装的开放许可。

已有可信开放行且不涉及恢复时，不必为普通代码升级重复关闭再打开。缺行或已关闭时，复用现有的签名恢复关闭、当前控制对账链。**不要直接 INSERT/UPDATE `closed=0`，也不要在迁移或启动脚本里自动补开放行。**

## 完整 journal、计划与关闭

以下命令在拥有外部私有目录的运维账号下执行。`EXTERNAL` 必须为实际已有的受保护目录，权限 0700；journal、密钥和输出文件为该账号所有，权限 0600、普通文件、无链接。密钥恰为 32 字节。`NC_CONTAINER` 为已核对归属的完整 64 位容器 ID。

```sh
umask 077
EXTERNAL=/absolute/private-external
JOURNAL="$EXTERNAL/journal.jsonl"
KEYFILE="$EXTERNAL/journal.key"
NC_CONTAINER=<exact-64-hex-nextcloud-container-id>
NC_PROJECT=<verified-compose-project>
```

在维护模式尚未抑制第三方 `occ` 命令之前，或经隔离运维路径使普通应用命令可用但业务入口与写入者仍被隔离时，收集到真实 head：

```sh
python3 scripts/ops/publication-recovery-journal.py collect \
  --journal "$JOURNAL" --key-file "$KEYFILE" \
  --container-id "$NC_CONTAINER" --compose-project "$NC_PROJECT"
python3 scripts/ops/publication-recovery-journal.py verify \
  --journal "$JOURNAL" --key-file "$KEYFILE"
```

要求 `complete_to_head: true`。保留已独立记录的检查点 pin 和本次 terminal pin，在独立运维记录中核对；不能在恢复后重新读取一份旧数据库并把其自报 head 当成外部权威。若是确实没有历史的新隔离实例，导出的真实空页也必须由 collector 认证并独立保留，不能手造一个空 plan。

```sh
CHECKPOINT_PIN=<independently-retained-checkpoint-record-sha256>
THROUGH_PIN=<independently-retained-current-record-sha256>
PLAN="$EXTERNAL/initial-control-plan.json"
python3 scripts/ops/publication-recovery-journal.py replay-plan \
  --journal "$JOURNAL" --key-file "$KEYFILE" \
  --checkpoint-record-sha256 "$CHECKPOINT_PIN" \
  --through-record-sha256 "$THROUGH_PIN" \
  --signed-plan-output "$PLAN"
```

输出路径必须是新的；工具拒绝覆盖已有签名计划。保留检查点 manifest 的独立 SHA，并用既有检查点验证流程确认其外部 journal anchor。若涉及 WeKnora 的还原，还须验证其独立 recovery inventory 及双方原始 scope，见 [双端恢复 gate](coordinated-publication-recovery-gate.md)。单独生成 plan 不证明检查点或双方数据已经验证。

启用 Nextcloud maintenance，将同一计划和密钥安全复制到容器内独立私有目录，所有者为 Nextcloud config owner，父目录 0700、文件 0600，不写入源码或普通业务数据目录。下面 `NC_PLAN`、`NC_KEYFILE` 是容器内的绝对路径，不能误用外部主机路径；命令以实际 config owner 执行，官方镜像通常为 `www-data`。

```sh
NC_PLAN=/absolute/private-operator/initial-control-plan.json
NC_KEYFILE=/absolute/private-operator/journal.key
docker exec -u www-data "$NC_CONTAINER" php \
  custom_apps/integration_weknora/appinfo/recovery-console.php \
  --plan="$NC_PLAN" --key-file="$NC_KEYFILE"
```

专用入口要求 CLI、config owner、维护模式、私有文件与正确 HMAC，再验证实例、stream、恢复前缀和计划边界。它建立 closed gate、停止来源并重放保守关闭事实；不会开放来源或启动服务。普通 `occ` 在维护模式中不加载这些第三方命令，不能通过临时关闭维护来替代专用入口。

## 当前 LDAP 身份先于 inventory pin

控制对账要求**当前真实 LDAP 管理员**，本地 `admin` 密码不满足此条件。先在受控维护通道调用当前 `IdentityMappingService::forUser(actorUid)`，确认当前 UID 的真实目录 GUID、账号状态和映射；不能用同名账号、恢复的会话或手工写映射代替。

如需在维护环境准备映射，受审核的私有 PHP 运维入口应复用 `appinfo/recovery-console.php` 的 config-owner/maintenance 检查及仅加载插件命名空间的 autoloader，再加载 `user_ldap`，通过容器取得 `IdentityMappingService` 并调用 `forUser($actorUid)`。本候选没有独立的初始化 gate 或身份映射 CLI，不能编造 `occ initialize-gate` 等选项。不要为此加载整个插件的普通 jobs/监听器。

**该映射动作必须先于抓取 inventory pin。** 首次 `forUser()` 可能新增身份映射及审计行；若先 pin 后新增，正确的对账会因 inventory 改变而拒绝。之后仍须保持配置、目录、ACL 和恢复元数据稳定。

## 新尾部、当前 inventory 与可信 reopen

关闭操作可能追加 journal，因此原计划不一定仍代表当前 head。用原已认证计划通过专用 CLI 导出关闭后的新增页：

```sh
AFTER=<last-externally-retained-next_sequence>
docker exec -u www-data "$NC_CONTAINER" php \
  custom_apps/integration_weknora/appinfo/recovery-console.php \
  --plan="$NC_PLAN" --key-file="$NC_KEYFILE" --export-after="$AFTER" \
  > "$EXTERNAL/post-closure-page.json"
python3 scripts/ops/publication-recovery-journal.py append \
  --journal "$JOURNAL" --key-file "$KEYFILE" \
  < "$EXTERNAL/post-closure-page.json"
```

外部目录中创建文件前设置 `umask 077`，且每次使用新输出名。每页验证成功后才推进 `AFTER`；若 `has_more` 为 true，继续逐页导出和追加，直到完整到 head。不得只保存最后一页或跳过缺页。对新 terminal record 另行独立保留 pin，用原 `CHECKPOINT_PIN` 和新 terminal pin 重新执行 `replay-plan --signed-plan-output=<新的latest-tail文件>`，安全传入容器。

使用原已认证计划的 `--inspect-state` 获得当前 `control_state`，独立复核所有表均存在并保留当前 inventory SHA。这里的 SHA 是**当前**控制库存，不是旧检查点库存：

```sh
docker exec -u www-data "$NC_CONTAINER" php \
  custom_apps/integration_weknora/appinfo/recovery-console.php \
  --plan="$NC_PLAN" --key-file="$NC_KEYFILE" --inspect-state \
  > "$EXTERNAL/current-nextcloud-state.json"
```

`CONTROL_PLAN_SHA` 指原关闭计划的 PHP 规范化计划 SHA，即 gate 和恢复回执所绑定的 `plan_sha256`；不是整个签名 envelope 文件的 SHA，也不是新 tail plan 的 SHA。使用独立关闭记录核对其值。`INVENTORY_SHA` 来自当前已检查的 `control_state.sha256`。

```sh
NC_TAIL_PLAN=/absolute/private-operator/latest-tail-plan.json
CONTROL_PLAN_SHA=<verified-original-closure-plan-sha256>
INVENTORY_SHA=<independently-pinned-current-control-inventory-sha256>
ACTOR=<current-live-ldap-administrator-uid>
docker exec -u www-data "$NC_CONTAINER" php \
  custom_apps/integration_weknora/appinfo/recovery-console.php \
  --plan="$NC_TAIL_PLAN" --key-file="$NC_KEYFILE" --reconcile-controls \
  --control-plan-sha256="$CONTROL_PLAN_SHA" \
  --control-inventory-sha256="$INVENTORY_SHA" --actor="$ACTOR"
```

对账要求最新外部 tail 精确匹配当前 instance/stream/sequence/chain、原关闭 plan 不变、永久 Tenant 删除事实完整、所有控制表存在、所有来源 stopped，并重新检查 live LDAP、根目录、ACL、内容版本 fence 与两次稳定库存。任一项失败都应保留关闭状态并调查，不得修改计划或库存使其勉强通过。

成功回执仍为 `sources_remain_stopped: true`、`ingress_reopen_permitted: false`。之后只能通过原有真实授权的显式 source resume 创建新 publication epoch，再做 WeKnora 当前签名配对、完整稳定 manifest、新代次及当前个人目录/ACL 对账。全局 gate 开放不等于可以开放业务入口。

## 恢复与永久删除事实

失败升级的恢复必须匹配代码、配置、文件和数据库权限。恢复旧检查点不能抹去检查点之前或之后独立保留的 `target_deleted` / `tenant_deleted` 事实，也不能删除相关表、丢弃 `tenant_namespace` 或降级 collector 来避开验证。新计划从完整保留历史提取永久事实；遗漏、冲突或未覆盖的事实应导致关闭和调查。

若旧版不能正确表达这些永久控制，不得仅切回旧二进制后开放来源。保持双方业务入口和 workers 停止，在兼容 schema/代码上完成认证关闭、完整事实重放及当前权威对账。必须保留失败与恢复证据，不能把手工恢复记作自动升级成功。

## 验收状态与最低回归

候选尚未完成真实双端验收，未因此批准升级生产。发布前至少验证：实际 PostgreSQL 的 0.6.0→0.8.0 迁移和恢复、manager 元数据 SQL、缺 gate 拒绝、签名关闭创建 closed gate、坏 HMAC/旧 tail/错 pin/缺控制表/无当前 LDAP admin 均拒绝、正确可信对账只打开全局 gate，以及永久删除事实跨恢复仍有效。

纯协议测试、事务回滚 fixture、使用 identity bypass 的测试或受控替身不能证明真实 LDAP、完整配对、WK/NC 双端授权和生产 HTTPS 已验收。每项结果应标明实际数据库、身份/网络边界、候选提交及未覆盖范围。

本候选的独占回归命令为：

```sh
python3 scripts/ops/isolated-manager-upgrade-smoke.py
python3 scripts/ops/isolated-manager-upgrade-smoke.py --gate-probe
COMPOSE_PROGRESS=plain python3 scripts/ops/isolated-upgrade-failure-recovery.py
```

前两项要求固定的 Nextcloud/PostgreSQL 镜像已缓存，不拉取镜像，不发布端口，不挂载主机业务数据，使用无外部路由的私有容器；结束时删除全部自有容器和卷。第二项还要求已预加载包含 `samba-ad-provision` 的 AD 镜像。默认使用本地验证过的不可变 image ID；在其他机器上，可先使用 `scripts/ops/fixtures/ldaps/Dockerfile` 准备专用测试镜像，再用 `--gate-ad-image=<已预加载镜像>` 指定，脚本会解析并固定其本地 image ID。旧的缺少 AD schema 的缓存镜像不适用。

第三项保留自己的随机 Compose 项目、loopback 端口、临时只读插件副本、匹配冷检查点和资源归属验证；其基线为无配对、无永久删除事实的 0.4.6，仅证明注入 Migration 29 故障后的匹配冷恢复，不证明永久事实的双端重放。
