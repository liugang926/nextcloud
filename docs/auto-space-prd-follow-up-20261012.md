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

## 同一处理结果的四个 HTTP 读取入口

新增可选 `--http-read` 联调：生产文档 handler 保留其实际数据库、原件与正文日志，独立 Gin handler 测试读取同一代次。详情、列表、预览、下载分别验证授权前 200、无本人身份 403、真实 Nextcloud LDAP share 删除后 403；预览/下载 SHA-256 与原件一致，响应 private/no-store，新增读取租约已释放。

列表最初拒绝源于夹具缺少 collection checkpoint migration，以及新建 KB 时没有通过显式名称输入记录显示来源。现使用正常 migration 和生产 writer API 补齐；未放宽授权。诊断代码移除后最终 runner exit 0，HTTP 12 子项通过（21.91 秒），同链路生产任务 38.52 秒。早期失败与诊断通过日志均保留。登录上下文仍为受控 Gin middleware，模型仍为受控响应，不能据此宣称完整应用认证、真实问答、Agent/history/cache 或 100 GB 验收。

WK 本地提交 `bec301dad`，候选补丁精确 apply/reverse 通过；临时 actor 确认不存在，来源 publication 关闭、私有文件清理，原件保留。[本轮收据](evidence/auto-space-prd-20261012/live-http-read-receipt.json)。

用户选择的本机现有模型配置已只读定位：当前 `weknora-ldap-local-postgres-1` 模型库存含本机 BGE 512 维嵌入及 DeepSeek chat 配置。尚未将真实推理接入本轮 fixture；密钥未写入证据或 Git。

## 本机真实 BGE / DeepSeek 同链路验证

已按用户选择复用本机现有模型库存。新增只读 `export_document_graph_models.py`，导出指定两条模型及源应用解密配置到 0700 目录中的 0600 文件；runner 的 `--model-config` 仅复制到私有临时目录。Go fixture 使用实际生产模型 factory，正确解密后按隔离库密钥落库，无法调用时直接失败，没有回退桩。现有应用和模型数据库均未修改。

使用本次创建的 Alice/Bob/Project Cedar Markdown 原件，真实 BGE 512 维嵌入、DeepSeek Flash 摘要和图谱抽取（各 1 次）完成。实际 SQLite 向量/FTS、SQL 图谱、生产阶段状态与 NC 签名 inbox 同链路通过；图谱重复投递复用持久 ACK，不重复调用模型。随后同一实际结果的 12 个 HTTP 权限断言通过，真实 AD 共享撤销后不可读，原件保持不变。业务链路 49.41 秒，HTTP 23.40 秒，runner exit 0；无真实模型配置的原回归也通过（2.589 秒）。

首次执行因本机模型 host 缺少明确 allowlist 拒绝，第二次暴露导出夹具没有解密数据库凭据而返回 401；均保留失败日志。最终只在独立 actor 中允许 `host.docker.internal`，没有关闭全局 SSRF 检查。导出工具实际读取结果与本次测试配置一致。私有配置已删除，临时两个 actor 确认不存在，日志检查未包含所检查凭据。

WK 提交 `3ed70eb5c` 已纳入冻结候选并通过精确 apply/reverse。[真实模型收据](evidence/auto-space-prd-20261012/live-real-model-receipt.json)。这证明小 Markdown 文件真实模型处理与权限链路，不代表本人完整问答、Agent/历史/缓存、PDF/OCR、Redis 调度、完整应用恢复或 100 GB 全链路验收；这些仍需继续。

复现时先在私有临时目录运行 `export_document_graph_models.py --database-container weknora-ldap-local-postgres-1 --app-container weknora-ldap-local-app-1 --embedding-id builtin-local-bge-small-zh-v15 --chat-id 845ddfda-a1c3-4294-bf3e-5955d45c40cf --output <私有目录>/models.json`，再给既有 live runner 追加 `--http-read --model-config <私有目录>/models.json --model-allowed-host host.docker.internal`。测试完成删除导出目录；不要将文件放入仓库或证据目录。

## 真实检索、流式问答与保留结果撤权

live runner 新增 `--rag`（必须同时传入真实 `--model-config`）。同一份 NC 原件处理完成后，实际 knowledgeBaseService 使用本轮 SQLite 向量/FTS 和真实 BGE 查询嵌入执行 HybridSearch，真实 SQL snapshot 验证/补全分块，再经生产 merge、提示词和 KnowledgeQAByEvent 流式插件调用 DeepSeek。测试同时检查提示词含源文档人物、答案正确包含 Bob、最终答案事件带完整非空来源依赖。

真实共享删除后，保留 turn 的当前权限检查拒绝；再次 HybridSearch 按接口契约返回空结果；重复使用缓存结果进行问答，在再次调用模型前拒绝，stream 调用计数不增加。完整 runner exit 0，业务链路 66.33 秒，12 项 HTTP 入口断言 25.32 秒。所有新 HTTP 租约释放，原件保持不变，签名 NC inbox 对账通过。

前三轮失败保留：初始思考/token设置未产出答案；知识服务测试夹具未接 publication guard 导致提示词剔除文档；测试把撤权检索错误要求为 error，而生产接口正确过滤为空。最终采用明确非思考模式和 512 token 上限、真实 publication guard，以及空结果契约断言，没有放宽生产授权。

WK 本地提交 `0ef060af5` 已冻结且补丁精确 apply/reverse 通过。[本轮收据](evidence/auto-space-prd-20261012/live-real-rag-receipt.json)。私有配置已删除、测试 actor 确认不存在。范围是实际 service/event 流式问答和内存 turn 缓存；持久历史消息、Agent、完整登录/API/browser链路和 100 GB 仍未据此验收。

## 真实答案持久历史与当前来源权限

runner 新增 `--history`，要求同时启用 `--rag` 与真实模型配置。测试按已有顺序安装 message materials / original body schema，再通过生产消息服务创建用户原始问题和待完成 assistant。真实 RAG 流式答案及最终事件来源证明通过 UpdateMessage 提交；未预制答案或通过原始 SQL 填入正文。

使用新的读取上下文，经实际 GetMessage、GetMessagesBySession、GetRecentMessagesBySession 从持久层重新读取，逐项校验正文与来源依赖。原件真实 NC LDAP share 删除后，已保留的历史正文 guard 重新检查拒绝，新详情、分页和最近消息读取均拒绝且不返回答案。RAG 缓存拒绝、12 个 HTTP 权限检查、文件字节/租约及 NC inbox 验证仍通过。

本轮首次完整 runner exit 0，业务 72.13 秒，HTTP 26.62 秒。WK 提交 `980be4d78` 已纳入冻结候选且精确 apply/reverse 通过；私有模型配置删除、测试 actor 确认不存在。[持久历史收据](evidence/auto-space-prd-20261012/live-real-history-receipt.json)。复现时给上一节真实模型命令追加 `--rag --history`。

范围是生产消息服务的持久历史接口，登录上下文仍由夹具提供；完整 JWT/browser 历史请求、跨会话搜索、Agent/fork/export、完整应用恢复和 100 GB 不由本轮推定通过。

## Agent 真实工具验收准备及 DNS 失败记录

runner 新增 `--agent`（要求 `--rag` 和真实模型配置）。源码 `ae90c6be4` 已编译通过：测试调用生产 AgentEngine、search_knowledge/read_document、实际知识库/片段服务与来源守卫，要求真实模型选择两种工具、答案和工具结果携带来源依赖，并在实际分享删除后拒绝带旧答案的下一轮；同时断言流式和非流式模型调用次数不增长。新增多轮预算只用于 Agent 路径，原有 runner 时限保留。

两次实际 runner 均在生产 chat factory 的 SSRF DNS 校验处失败（api.deepseek.com），业务阶段分别 9.45 / 9.54 秒，均未进入本轮处理和 Agent，不能记工具或撤权验收通过。之后宿主机、NC PHP，以及同镜像/同网络 getent 只读探针均能解析，Go 解析差异仍需定位；没有扩大 DeepSeek allowlist 或关闭 SSRF。另一个待实测点是 Agent/工具内部上下文结束后，最终 HTTP 发送所需的物理读取保护是否仍有效，尚未据静态分析修改生产生命周期。

[本轮失败收据](evidence/auto-space-prd-20261012/live-real-agent-dns-failure-receipt.json) 固定源码和日志。两次进程已终止，owned actor 不存在，NC publication/私有 transport 清理，模型导出删除。冻结候选仍为前一轮实测通过的 `980be4d78`，没有将此次未通过路径更新为已验收候选；完整目标继续进行。

## Agent 环境修复与独立生命周期诊断

同镜像、同网络的 Go 1.26.8 标准库探针分别使用默认及 cgo 解析器，均成功取得 DeepSeek 公网 IPv4；IPv6 无记录。没有修改解析器配置或放宽 SSRF，前两次 DNS 故障暂未复现。

独立生命周期测试尝试增加工具上下文完成后继续 HTTP 输出的断言，但九个分支均在旧 history 公共夹具初始化处失败（`knowledge_history_actor_integration_test.go:84`），未到新增断言。失败补丁和日志保留；新增断言改为使用现有机器文档处理夹具，执行仍待资源窗口。这不证明生产生命周期问题已发生或已修复。

第三次真实 runner 在 NC prepare 阶段失败，未创建 Go actor。检查发现隔离 AD 证书于 `2026-10-12T04:25:53Z` 到期。新增 `renew-running-test-tls.py` 仅支持经过所有权、无宿主端口和 sleep 监督方式检查的既有隔离容器；更新私有证书、重启其中 Samba 并更新 NC 公共 CA，未重建目录或关闭 TLS 校验。实际 NC `ldap:test-config s01` 通过，三个测试用户 GUID 前后相同，新证书有效至 `2026-10-14T07:50:16Z`。临时模型导出已删除。

[环境修复收据](evidence/auto-space-prd-20261012/agent-environment-repair-receipt.json) 记录上述实际结果。Agent 工具调用和撤权验收仍未通过，冻结候选未更新。

## 工具结束后 HTTP 读取保护的生命周期修复

使用现有机器文档夹具复现：生产同步、原件日志、处理及真实 SQL 分块/租约完成后，工具上下文结束使 HTTP 持有的读取保护一起失效。修复前七项运行、五项通过；`producer-finished` 和 `producer-borrowed` 都在外层请求仍有效的输出检查处返回 `context canceled`。这次失败到达了实际目标断言，与此前旧 history 夹具初始化失败不同。

WK 提交 `aa6801173` 将回合持有的物理读取保护绑定到外层请求的取消和截止时间，保留当前消费上下文的身份等值；每次读取仍由 `ContextFor` 绑定当前调用取消，输出边界也明确拒绝已取消的消费上下文。未使用脱离取消的后台上下文，未移除当前来源或租约检查。

同一组七项重跑全部通过（12.16 秒）：正常检索/合并、两种工具结束场景、外层请求取消、源撤权、缺少来源证明以及当前调用取消。工具结束后 HTTP 保护仍有效，模型和 HTTP 最后引用释放后实际读取租约归零。测试 actor 已确认不存在。[修复收据](evidence/auto-space-prd-20261012/agent-physical-lifetime-receipt.json) 固定修复前后日志与源码。

本轮为 SQLite 实际 service 回归，模型、向量候选和来源接口响应由夹具控制；不能替代真实模型 Agent 工具循环、完整认证 HTTP 或 PostgreSQL 验收。冻结候选暂留 `980be4d78`，等待真实 Agent 联调后更新。
