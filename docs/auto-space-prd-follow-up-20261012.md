# 自动关联 PRD 补充实现与验证 — 2026-10-12

## 本轮代码

在已有 `codex/auto-space-prd-20261010` 独立实现上继续补缺。Nextcloud 路径为 `/Users/abner/My_Project/ai-server/nextcloud-auto-space-prd-20261010`；配套 WeKnora 为 `/Users/abner/My_Project/ai-server/WeKnora-auto-space-prd-20261010`。原始工作目录中的 PRD 保持不变，未覆盖主开发目录已有变更。

1. 五秒目录 worker 现在处理持久关联意图，修复原先只发现目录、配对仍等待普通 cron 的问题。保留每操作退避和处理租约，自动任务不会替换用户目标选择。
2. 普通队列及实际领取 SQL 仅接受 pending/active。退休意图不再被领取；统一元数据更新拒绝修改 retired，防止迟到失败改回 pending 或迟到成功改回 active。
3. Nextcloud 维护模式及协调恢复 control gate 统一拦截开通自动化。规则修改、创建、关联、重试、切库、同步开关、扫描、队列消费、注册锁内写入及每次 onboarding HTTP 请求检查门禁。

未修改源文件正文、ETag、生产目录权限、共享运行栈或 GC coverage。代码复制及动态验证仅使用带 `codex.task=auto-space-prd-20261010` 标签的现有隔离 Nextcloud/PostgreSQL fixture。

## 实际验证

- 新增 `directory_worker_recovery_runtime.php`，使用真实 Nextcloud 容器和 PostgreSQL 事务。旧代码先实际失败：`A retired intent was claimed or rewritten by an old worker`；修复后通过退休保护、迟到成功保护、真实 CLI 调度、退避和恢复关闭期间库存不变断言。该测试事务最终回滚，不发远端请求。
- 目录关联与重试、目录发现和显式目标优先、状态 inbox 与 ETag 不变，共四个运行时测试通过，见 [运行收据](evidence/auto-space-prd-20261012/runtime.json)。既有目录测试使用明确开启的 synthetic identity fixture，不代表企业 LDAPS 验收。
- 12 项 PHP 契约测试全部通过，见 [契约收据](evidence/auto-space-prd-20261012/contracts.json)。六个修改的 PHP 文件语法检查及 `git diff --check` 通过。
- WeKnora `auto_space` 冻结补丁实际 apply/reverse 精确验证通过，candidate tree 为 `f988fd2f41ce277080be96ccfe20938ea14ace4a`。原补丁三处空白告警仍在，未篡改历史补丁。这是本日首轮的验证记录；下节记录随后发现并修复的 WeKnora 实际同步问题。

- WeKnora 三个包的定向测试通过：repository、middleware、types，覆盖选中的 onboarding/processing/graph/scoped/required 测试。实际命令 exit 0，见 [定向日志](evidence/auto-space-prd-20261012/weknora-focused.log)。没有把跳过的 live/opt-in 测试记为通过。
- 本轮实际两端配对与切库重新通过，业务测试 6.84 秒，runner exit 0。Nextcloud 使用修改后的代码，WeKnora 使用冻结候选；签名配对、来源 grant、根改名、跨 Tenant 新 KB 移交、精确重试、旧配对退休及零普通空间成员断言通过。使用空根与 synthetic WK 身份/模型库存，见 [两端日志](evidence/auto-space-prd-20261012/onboarding-actual-wk.log)。

- 实际图谱 provider 成功持久化，以及模型处理后、provider 提交前发生撤回时不写入的两个服务回归通过（package 0.943 秒，exit 0）。使用受控抽取模型与真实 SQL 图谱实现；临时 Go 测试容器已停止并自动删除。

[本轮收据与源码 SHA-256](evidence/auto-space-prd-20261012/follow-up-receipt.json) 将复现失败、修复验证和代码对应起来。

## 实际文档任务链补充

继续使用用户指定的本机隔离环境后，实际同步复验发现：流式同步把发布代次写入已封存的 fetched item，导致正常下载原件被完整性校验拒绝。现改为绑定 source ID 的内部上下文传递，在独立 knowledge metadata 中保存代次；原件封存与当前来源校验保持严格。

升级原实际 pipeline 测试夹具到当前 schema/processing ledger 后，实际同步、原件落盘、解析、分块、后处理、摘要、标签和本人读取/撤权的业务任务链通过 SQLite（1.96 秒）和 PostgreSQL（4.51 秒）。另通过 14 个处理期间变化/撤回场景及日志保护。来源 HTTP、模型和检索 engine 仍为受控夹具，不能据此宣称完整真实 Nextcloud+LDAPS+模型联合验收。

由运行日志发现的正文预览泄露也已修复：受保护来源的普通日志仅保留固定诊断，直接 logrus 调用不输出内容；未标记日志行为保留。实际 sink 验证覆盖格式化、结构化、继承 metadata 及 detached context。

最终定向 8 个父测试与 PostgreSQL pipeline exit 0；扩大选择中的两个旧 mock 失败保留，均已列入原基线失败审计，没有冒充全套通过。[验证收据及代码哈希](evidence/auto-space-prd-20261012/machine-pipeline-receipt.json) 保存首次失败与逐步修复结果。

WeKnora 新本地提交为 `e73726a9d`。`auto_space` manifest 与完整补丁已更新并重新通过精确 apply/reverse 验证；原 c6/rag 候选不变，未部署共享栈。旧万文件图谱指标明确保留为旧候选的历史证据，不借给新源码。

## 文档到实际索引和图谱的同链路补充

新增 `TestNextcloudDocumentToSQLIndexGraphAndCurrentState` 从实际流式同步入口开始，不预制 chunks、成功 spans 或已发布 candidate。生产文档和后处理 handler 生成实际任务，写入 SQLite 向量/FTS 索引，并消费真实入队 payload，将图谱写入受版本和租约保护的 SQL backend；最终检查 parse/chunk/embedding/index、required graph、published 状态及 outbox。重复投递图谱任务必须复用持久 ACK，不能重复调用模型。

首次验证因 Go 构建缺少 `sqlite_fts5` 模块失败；使用项目 Lite 构建所需的 FTS5 tag 后通过（业务 2.58 秒）。测试文件带同名 build tag，避免未启用模块的测试构建误执行。原失败保留在 `document-graph-initial.log` / `document-graph-diagnostic.log`，成功见 `document-graph-fts.log`，均在本日 evidence 目录。

来源 HTTP 和抽取/嵌入模型仍为受控夹具；实际索引、图谱及状态持久化已覆盖，但还不能算真实 Nextcloud inbox 回写、真实模型质量或 100 GB 容量验收。

最终两项任务链回归 exit 0（package 4.381 秒），本地测试提交 `80f4258f9` 已纳入 `auto_space` 候选，补丁精确 apply/reverse 通过。见 [同链路验证收据](evidence/auto-space-prd-20261012/document-graph-receipt.json)。

## 真实 Nextcloud 原件到状态 inbox 联调

新增 `apps/integration_weknora/tests/run_document_graph_live.py` 与 PHP 配套入口，以及 Go `TestNextcloudLiveDocumentToGraphAndInbox`。准备阶段创建独立原件目录、发布代次和唯一 KB 配对；仅使用指定隔离 NC 容器，Go actor 限制为 1 CPU / 2 GiB。私有配对资料通过临时文件传递，不写入证据日志。

这次使用真实 NC HTTP 原件和签名校验，不再预建分块或手工标记处理阶段。实际流式同步、原件落盘、生产文档/后处理 handler、SQLite 向量和 FTS5、受租约保护的 SQL 图谱、状态 dispatcher 和 NC 持久 inbox 形成同一条链路。接收端按 v2 协议规范化后逐字段匹配发送结果，并检查所有阶段 succeeded、required graph persisted、published，以及原件 ID、ETag 和 SHA-256 未改变。

准备与验证过程中发现并修正了测试适配错误（Go 值/指针、有效发布代次、私有文件所有者、协议规范化比较），没有放宽生产授权、原件证明或状态协议。初次完整 Go 链路通过 12.70 秒，接收端规范化断言修正后实际校验通过；原运行记录保留在本日 `live-document*` evidence 目录。抽取/嵌入模型仍为受控响应，队列由测试顺序消费，SQLite 库为隔离临时库；不代表真实模型质量、Redis 调度并发或企业目录验收。

重跑命令：`python3 apps/integration_weknora/tests/run_document_graph_live.py --source-repo /Users/abner/My_Project/ai-server/WeKnora-auto-space-prd-20261010 --evidence <新的证据目录>`。需要现有 `weknora-prd-live-processing` 网络及带指定 task 标签的 NC fixture；测试仅创建自己的有界 Go actor。

最终完整 runner exit 0：业务链路 14.55 秒，NC 独立持久结果验证通过；4 项任务链/图谱回归 package 5.405 秒。测试后关闭本次发布并清除私有传输文件，保留原件。WK 提交 `4099480bd` 已纳入冻结候选并通过精确补丁验证。[本轮收据](evidence/auto-space-prd-20261012/live-document-receipt.json)。

## 同一实际处理结果的真实 LDAPS 撤权

本轮将新建原件归属改为隔离域真实 LDAP owner `prd-sourceowner`，通过正常 Nextcloud 用户共享授予 `prd-alice` READ。从实时 IdentityMapping 取得 directory ID / objectGUID 后，测试 WK actor 映射到此身份；WK directory snapshot 仍为受控记录，NC 的 LDAPS identity / account / effective READ 检查是真实调用。

完整链路继续写入实际索引、图谱与 NC inbox。撤权前，WK KnowledgeOutputAuthorizer 和 ChunkOutputAuthorizer 均允许同一已处理结果，NC EmployeeFileStatus 与 FolderSummary 也返回可见文件。删除实际 NC share 后，保留的 document/chunk guard 重新检查并拒绝；新建 knowledge read 也拒绝，即使测试 actor 的 WK role 为 admin。NC 状态及汇总同时拒绝，原件 ID/ETag/hash 保持不变。

最终完整 runner exit 0，业务链路 20.42 秒；证据为 `live-document-permissions-final/`。测试后关闭本次 publication、删除私有传输文件、保留原件；未将 share 撤销冒充物理 GC。这证明此处两个实际输出门禁和两个 NC 元数据入口，不替代 Agent/history/cache/download 的逐入口验收或 WK 全量 LDAP snapshot 同步。Nextcloud 调度/恢复门禁修复现已保存本地提交 `642c4f1`。

WK 撤权联调提交 `26f9187d0` 已纳入当前候选，精确 apply/reverse 验证通过。[权限联调收据](evidence/auto-space-prd-20261012/live-permissions-receipt.json)。

## 当前协调恢复演练的前置失败

当前源码重跑 `isolated-coordinated-recovery-smoke.py`，尚未执行任何数据库回滚。首次和第二次在 Docker 默认 subnet pool 耗尽时失败；脚本新增私有失败输出（合成密码脱敏），并为两个一次性网络选择与现有 Docker 网络不重叠的 benchmark-range `/28`。保留全部其他任务网络，重叠及耗尽检查通过。

第三次实际启动了隔离 NC/OpenLDAP/两端 PostgreSQL/Redis，随后在身份映射前置阶段收到 HTTP 400：旧夹具用户仅具备 `adTestUser`，当前实时 AD verifier 要求真实 AD `objectClass=user`。未开启 unverified identity 绕过。每次失败均走带 nonce 标签的精确清理；诊断记录保留，结果明确 INCOMPLETE。下一步需将该恢复夹具改为真实 Samba AD，再验证当前 schema 下的双端备份/恢复与新代次重建。[本轮恢复收据](evidence/auto-space-prd-20261012/coordinated-recovery-current-receipt.json)。

## 未完成的完整验收

本轮修补不能把 [A01–A40 验收映射](auto-space-prd-acceptance-map-20261010.md) 中的受控测试提升为生产验收。真实 NC 原件到实际索引/图谱/状态 inbox 已补齐，但使用受控模型；实际解析/嵌入服务及本人问答的完整联合链路、开放入口逐项撤权、完整协调恢复，以及 100 GB 文档处理容量仍需在指定测试环境完成。旧 100 GB 原件下载的八次失败保持历史失败；高级 ACL 为单独扩展。目标保持未完成，不将现有实现分支等同全部 PRD 验收通过。

## 真实 Samba AD 恢复演练与日志连续性修复

协调恢复 runner 已改为独立 Samba AD LDAPS，真实 publisher 是来源 owner 与隔离环境管理员，Alice 通过嵌套部门组取得 READ。新增独立 AD 镜像构建；CA 只安装到本次 NC 容器，未关闭 TLS 校验，未启用身份绕过。

实际双端 pg_dump/pg_restore 后暴露两项生产问题：维护命令没有加载 LDAP 后端，无法解析真实管理员；关闭重放从回滚后的旧日志头追加，造成与外部保留链分叉。现在显式控制对账才加载 LDAP；签名计划包含原始事件元数据，恢复前逐项验证并原子补回缺失日志，然后追加关闭事件、外部留存新尾部，再以最新独立 pin 做控制对账。全局控制与每个来源的开启仍分阶段校验。

PHP 独立回滚事务测试证明篡改、缺项和错误前缀拒绝且不留部分写入，重复应用幂等，后续关闭追加保持外部前缀。17 项 Python 日志测试通过；Go 恢复定向回归（含新增事件篡改/缺项/重排/缺必填字段）通过，package 0.736 秒。恢复演练第 10 次已通过原阻断点，第 11 次证明当前版本撤权前可读、删除真实部门共享后返回 `current_directory_membership_denied`；测试旧断言仍要求 `source_not_readable`，已按实际授权入口修正并重跑，不修改生产拒绝逻辑。

WK 提交 `06c6bfd45` 已更新冻结候选，精确 apply/reverse 通过。此次是实际 NC + LDAPS + PostgreSQL 上的 CLI/repository 恢复验证，WK 索引仍为关系库测试记录，不代替完整应用、外部向量后端、真实模型或 100 GB 验收。未开放共享入口。

最终第 12 次 runner exit 0，Go 实际演练通过，nonce 所有权清理验证通过。双端关闭、最新外部日志链、新代次发布、无 actor 拒绝、内容版本变化拒绝，以及真实部门共享撤销均完成。[验收收据](evidence/auto-space-prd-20261012/coordinated-recovery-ad-receipt.json) 固定测试源码及日志哈希；[实际运行收据](evidence/auto-space-prd-20261012/coordinated-recovery-ad-12.json) 保留完整阶段边界。全 PRD 目标仍未完成。
