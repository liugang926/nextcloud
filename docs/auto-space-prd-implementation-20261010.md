# Nextcloud / WeKnora 自动关联与来源权限实现

本次实现对应 `nextcloud-weknora-auto-space-permissions-prd.md`，以独立开发分支交付。目标是已有 Tenant 内的一根一专用 KB；自动关联不创建 Tenant、不建立普通空间成员关系。生产启用须同时升级两端，应用全部迁移并配置受限服务凭据。

## 2026-10-12 补充修复

复验现有独立分支后，修复了目录发现 worker 未消费关联意图、退休意图被普通队列重新处理，以及恢复期间开通自动化缺少统一门禁的问题。新增真实 Nextcloud/PostgreSQL 回归先在旧代码复现退休状态被改写，再在修复代码通过。见 [补充实现与验证](auto-space-prd-follow-up-20261012.md)。原证据的模型、目录与容量边界保持不变。

## 实现范围

| 能力 | Nextcloud | WeKnora |
| --- | --- | --- |
| 目录创建与属性关联 | 同一持久意图、显式选择优先、Folder 详情侧栏、规则及委派配置 | 既有空白 KB 资格、专用 KB 创建、原子独占预留、目标委派与配额 |
| WebDAV 自动发现 | 节点事件仅写队列，五秒发现 worker，持久扫描游标 | 纳管同一操作，处理当前 source，不自动新建 Tenant |
| 配对及重试 | 事务内加密 token 交接，原 UUID 重试，独立事件连接 | 持久预留、签名 commit、ACK 未知对账，不因空页面删历史 |
| 用户及组权限 | 活跃 AD GUID、LDAPS、直接/嵌套/主组、真实本人 Files READ | 每来源主体 ledger、来源限定进入资格、保护性禁用，保留手工及其他来源资格 |
| 全入口权限 | 签名文件授权与根授权、文件及目录状态前后检查 | 当前文件版本/发布/租约检查，KB 元数据根授权，私有来源问答，未适配入口关闭 |
| 当前处理状态 | 持久 inbox、签名快照、乱序去重、当前 head 投影、过期结果待验证 | 真实阶段台账与 outbox、当前 attempt/config/head、独立状态重试 |
| 文件图谱 | 独立显示图谱结果与 optional/required 要求 | `scoped_sql_v1` 真实节点关系存储、任务清单、稳定操作 ACK、实际写入 fence、精确 GC 库存 |
| 汇总及健康 | 可见当前文件、稳定 generation 分页、固定错误码及管理员健康告警 | 不以历史版本或全库计数冒充当前来源统计 |
| 迁移与恢复 | 根迁移及切库持久协议、旧状态关闭、恢复版本下限 | 旧租约及旧源关闭、新代次核验、控制库存及恢复门禁 |

首期支持经冻结适配器验证的本地 home 和 Team Folders 基础权限；高级 ACL、后代单独分享及未知挂载模式关闭来源。图谱真实后端为来源限定 SQL provider。其他图谱后端需要独立授权、fence 和库存适配；本次没有放宽全局物理 GC coverage 门禁。

## 数据库及兼容性

Nextcloud 应用版本为 `0.6.0`，新迁移为 `0023` 至 `0028`；WeKnora PostgreSQL 为 `000155` 至 `000161`，SQLite 为 `000074` 至 `000080`。新二进制、对应迁移和前端必须一起发布。本机已实际执行标准 `occ upgrade`，确认版本、全部六项新迁移及退出维护模式。

新增 `source_content_revision`：Nextcloud typed 文件写入事件在写入前持久推进版本并关闭旧版本，完成后解除该次写入标记。下载、发布检查、本人授权、状态回写及图谱原始输入都核对这个版本，防止同大小、同秒写入产生相同 ETag 时读取旧索引。写入中或无法核验时保持关闭。机器下载还核对 `X-Nextcloud-Content-Revision`。旧协议缺失值按 `0` 解释，不能读取已推进版本的文件。

已持久的旧 schema-2 状态缺失默认零版本时，只在旧业务 hash 完整、标准化后业务状态完全一致的情况下刷新认证时间；不会接受新内容代次、损坏 hash 或变化的阶段结果。

来源用户只取得临时 `scoped_only` 进入资格；服务端没有向 `tenant_members` 写入普通 Viewer。前端 `/source-knowledge` 仅展示获准来源 KB 和私有问答。后端继续校验每次内容、历史和输出，不依赖前端导航限制。恢复后若来源开通表缺失但当前模块标记仍存在，拒绝进入资格，不能回退成 legacy Admin 或自动晋升 Owner；完整旧 schema 的原行为及合法直接成员角色有独立数据库回归。来源 KB 的富文本简介、旧生成 profile、模型配置及未经完整当前文件核验的全库统计不作为根授权响应输出。普通手工移动候选、组织共享列表及其知识库计数排除来源托管 KB；这些列表没有逐文件的本人授权证明，来源内容继续由专用入口发现和读取。分类使用本地来源库存及历史标记，库存无法核验时关闭，不逐候选发远程请求。

## 配置与运行

1. 参考 [目录配置说明](nextcloud-directory-auto-onboarding.md) 建立允许的 Tenant、处理模板、服务 origin、凭据 ID、明确发布委派和目录规则。
2. 参考 WeKnora 的 `docs/nextcloud-onboarding.md` 注册精确 instance/origin/Tenant/template 的自动化凭据。凭据与一次性配对 token 留在服务端，浏览器不取得这些值。
3. 运行 Nextcloud 标准后台任务，并配置发现与状态补偿 worker。标准五分钟 cron 本身不满足十秒事件目标。
4. 模板启用图谱时选择受支持的来源 SQL provider；required 图谱未完成时不得发布文档。

```sh
NEXTCLOUD_OCC_PATH=/var/www/html/occ sh scripts/ops/directory-discovery-worker.sh
NEXTCLOUD_OCC_PATH=/var/www/html/occ WEKNORA_STATUS_WORKERS=4 sh scripts/ops/stage-status-worker.sh
```

worker 由安装的 web 用户运行并交给服务管理器管理。发现只排队；状态 worker 每五秒处理有界批次，首次回写全部丢失时仍可从真实 filecache 发现文件。pending token 以 CAS 完成，迟到 ACK 不消耗后来的刷新提示。外部请求使用精确 origin、TLS 与用途独立签名；状态成功不授权本人问答。

## 本机验证与边界

所有动态测试只操作本次隔离 fixture。Nextcloud 与 AD 服务使用 `codex.task=auto-space-prd-20261010` 标签；Go 测试容器按协调窗口的精确 actor ID、资源上限和实际终态留证。实际 Nextcloud 34.0.4、PostgreSQL 16、Samba AD LDAPS 和 Files 分享挂载用于当前来源验证；企业 Microsoft AD、生产配置和生产恢复未据此验收。

已验证的组件包括：原子预留与并发、原操作重试、来源限定资格、真实直接/嵌套/主组及撤权、坏证书/离线关闭、共享路径归一、实际相同 ETag 写入版本变化、根迁移、文件状态回写及签名/重放/跨 KB 拒绝。各项验证的真实与受控边界分别记录如下。

| 验证 | 实际边界 | 结果记录 |
| --- | --- | --- |
| LDAPS 来源四入口 | 真实 GUID / group / NC mount / root+file+state+summary | [来源验证记录](auto-space-prd-ldaps-source-receipt-20261010.md) |
| 来源问答浏览器 | 构建产物、真实 Chrome、受控 API fixture、撤权隐藏历史及手机宽度 | WeKnora `docs/evidence/auto-space-prd-20261010/frontend-validation.json`；typecheck、生产构建、13 个语言文件均通过 |
| Nextcloud 目录与文件侧栏 | 实际 NC 34.0.4、LDAP 用户、真实 Files 页面及真实 v2 阶段 | [浏览器记录及截图](auto-space-prd-nextcloud-browser-20261010.md) |
| 10,000 文件目录汇总 | filecache 元数据规模，非 100 GB 实际内容 | [目录性能记录](nextcloud-directory-auto-onboarding.md) |
| 10,000 条状态回写 | 真实 HTTP/HMAC、两端 SQLite、逐事件 durable receiver ACK | `docs/nextcloud-state-throughput-10000.log`（WeKnora） |
| 真实图谱及两端回写 | 实际业务 pipeline、SQL provider、NC inbox；受控模型，精确恢复已持久 receiver checkpoint | WeKnora `final-short-live-checkpoint-20261010T1037.log`：1.29 秒通过实际回写及重复 ACK；无 receiver reset |
| 10,000 文件 SQL 图谱处理 | 预置解析分块、每文件一个实际 chunk、受控源/模型、跳过 embedding；真实输入证明、租约、SQL graph、持久任务 ACK 及发布状态 | WeKnora `processing-real-10000-full-window-receipt.json`：完整 10,000 数量断言通过，业务 1143.783 秒，8.74 文件/秒，P95 145 ms；单 1 CPU / 2 GiB，cgroup 峰值 2,100,015,104 字节（含编译），无 OOM。首轮 9,368 完成后超时的 FAIL 独立保留 |
| 10,000 文件 / 100 GB 原件 | 实际 WebDAV 原件、物理 stat、签名分页清单与流式摘要 | [压力记录](auto-space-prd-source-pressure-20261010.md)：上传 10,000/10,000 成功；下载 9,992/10,000 校验成功，8 次断连（0.08%），未记为全部通过 |
| 切库当前管理者 | 实际 NC34/Pg16/可信 LDAPS；受控远端协议 | [撤委派及当前 READ 验证](evidence/auto-space-prd-20261010/target-migration-current-authority.json)：撤权零调用，真实 LDAP 管理员需本人来源 READ，以同 UUID/IDs 恢复；不计为实际两端退休验收 |
| 实际两端配对及切库 | 实际 NC 与 WeKnora handler/repository；空根及受控目标目录、主体、模型 | WeKnora `processing-sixth-final-window-receipt.json`：6.51 秒通过签名预留、配对、事件连接、授权、稳定根改名、跨 Tenant 移交和原操作重试；原件不变且无普通 TenantMember |
| 协调恢复 | SQLite / PostgreSQL 持久门禁、typed journal、独立版本下限及控制库存 | WeKnora `final-recovery-positive-rerun.log` 及 `final-recovery-regressions.log`：维护构建、实际 SQL graph fence、旧代次退休及完整清单 CAS 回归通过；生产恢复及外部后端另验 |

受控模型和模拟 API 的测试不会被列为生产模型或完整业务环境验收。万文件图谱测试预置解析/分块结果并跳过 embedding，实际运行原始输入证明、租约、Chunk SQL 存储、图谱任务及模型调用、SQL provider、持久 ACK、发布和状态投影；它不衡量生产 parser、OCR、splitter 或 embedding 容量。100 GB 原件 IO、图谱任务和状态处理分别测量；不会把声明的 filecache size 当作实际写入字节，也不会把状态 inbox ACK 当作图谱或物理 GC 完成证明。100 GB 下载的断连已通过真实 HTTP 重连回归修复，但原件清理后没有第二轮全量下载，不能据此补记原轮失败为成功。

当前源内容同时关闭 LLM debug 正文文件和 Langfuse 正文遥测；chat、embedding、rerank、VLM、ASR 与 detached context 的九项父测试均通过真实文件/HTTP/log spy 验证。隐私限制也覆盖安装 RAG 账本上下文的普通 RAG/Agent 会话，其模型 generation 与用量遥测停写；无标记直接模型调用保留原追踪。构建隐私标记不赋予来源访问权。三处候选查询显式保留非空条件，命中现有 SQLite partial index；实际 Go SQLite 查询计划和图谱成功/撤回回归通过，无新增生产索引。

SDK 与调用层的补充审查修复了包装器测试未覆盖的完整请求、raw dump、流内容预览、供应商错误及 RAG 公共日志路径。四 SDK 的真实 HTTP 流式/非流式矩阵、原生 Ollama 错误和实际 completion/SSE 消费测试均通过；保护上下文没有正文日志或 raw 目录，原请求、响应、错误及取消控制保持。两轮独立收据为 WeKnora `sdk-content-privacy-window-receipt.json`、`caller-content-privacy-window-receipt.json`，分别有三项和四项新父测试，最终 141 文件格式检查通过。原万文件图谱测试的 126 源文件字节未变，指标保留其原始冻结哈希；补充测试没有据此声称所有第三方 tracing 或生产模型已验收。

恢复时独立的控制门禁同时关闭来源限定用户和普通 Owner 的内容、根元数据及读租约。只有与当前恢复计划、完整签名来源清单、新 KB 代次和未退休候选完全一致的维护构建，才能取得持久的专用构建许可；SQL 图谱实际写入也检查该许可。构建成功保持发布“待验证”，直到全部来源及当前控制库存核验后以 CAS 解除控制门禁。旧内容 fence 保持退休，凭据与托管授权仍需显式重新核验。

历史基线本身存在 45 个失败父测试。本次引入的回归单独修复和验证，完整历史套件不能记为全绿；WeKnora 的 `docs/evidence/auto-space-prd-20261010/baseline-regression-audit.json` 保存精确基线、失败名称、日志哈希和 OOM 边界。

## PRD 验收对应关系

下表列出本机验证对应的工作包和验收范围，不能代替企业目录、真实模型与生产试点的完整验收。

[逐项 A01–A40 验收映射](auto-space-prd-acceptance-map-20261010.md) 列出每项的实际证据、受控测试边界和未验收范围；最后的两端配对及万文件图谱结果以冻结 receipt 为准。

| PRD 项目 | 实现与证据范围 |
| --- | --- |
| A01–A08：开通、空白、并发与重试 | 两端持久意图、virgin 库存证明、独占预留、逐操作加密交接；SQLite 与 PostgreSQL 并发测试，真实浏览器提交；完整两端联调结果单列 |
| A09–A12：来源授权与目录故障 | 逐来源 ledger、来源限定进入资格；真实 Samba AD LDAPS 的直接/嵌套/主组、当前 Files READ、撤权、离线与坏证书；目标目录快照和 pre-login 委派由受控 fixture 验证 |
| A13–A18：目录、版本与同步开关 | 稳定物理根迁移、切换目标、新旧代次屏障、同 ETag 内容版本；Folder 配置、继承、过期 revision 拒绝；同步暂停与发布停止分离 |
| A19–A23：文档与图谱 | 实际来源读取、受控文本/模型、业务 extraction、真实 SQL graph provider 与持久 ACK；optional/required、失败/无实体/未启用独立判定；远程模型、OCR 和大文档容量另验 |
| A24–A30：状态与汇总 | 实际签名 inbox/outbox、乱序/重放/ACK、缺失首次推送补偿、旧协议兼容、当前本人可见汇总；真实浏览器显示阶段事实和过期状态；只写插件元数据 |
| A31–A38：回收与恢复 | 精确资源 claim/ACK、引用和租约、保护性禁用；SQL provider 实际写入触发器及回收测试；SQLite/PG 正向恢复、完整清单、控制库存 CAS、普通 Owner 关闭与旧 ID 退休；外部 coverage 未验收仍阻塞 |
| A39：压力 | 原件 10,000 文件/100 GB、10,000 条 HTTP/HMAC 状态、微文本 SQL 图谱分开计量；原件下载保留 0.08% 失败，正常上传探针 100/100 成功且没有因果 baseline |
| A40：高级权限扩展 | 尚未适配；高级 ACL、后代单独分享、未知挂载保持明确关闭，不以固定 allow/deny 模拟支持 |

## 2026-10-10 开发候选（历史）

当前构建候选已在 2026-10-12 更新为 `e73726a9d`，修复流式原件证明和来源日志，详见 [补充报告](auto-space-prd-follow-up-20261012.md)。下列哈希及性能证据保留原冻结版本；实际构建以当前 manifest 为准。

WeKnora 已冻结为提交 `cf2a14b0272d6bfe1af0b9480e54df3923c3bff0`，tree 为 `f988fd2f41ce277080be96ccfe20938ea14ace4a`。独立分支为 `codex/auto-space-prd-20261010`；Nextcloud 交付也在同名独立分支。`integration/candidates/manifest.json` 新增 `auto_space`，基线为精确 `c6c4bd445a8ee49e742da9d804957a3fe4bf52d4`。

[完整补丁](../integration/candidates/weknora-auto-space.patch) 为 9,563,801 字节，SHA-256 为 `862b8c333e863994279d5673b1b81767951533194663f6106b395832d7614765`。实际 `--verify-only` 已通过：临时 index 应用后 tree 与冻结 tree 完全一致，反向应用恢复精确 base tree；传入 checkout 保持原状。[验证收据](evidence/auto-space-prd-20261010/candidate-verification.json) 保留结果。原 `c6`、`rag` 的 manifest 对象、补丁字节及哈希均保持不变。未运行新的 app/UI Docker build，未改变共享栈、全局入口或外部物理 GC coverage。

```sh
WEKNORA_RAG_SOURCE_DIR=/absolute/path/to/WeKnora \
  bash scripts/build-weknora-auto-space.sh --verify-only
# 容量检查通过后构建新的独立 app / UI 标签：
WEKNORA_RAG_SOURCE_DIR=/absolute/path/to/WeKnora \
  bash scripts/build-weknora-auto-space.sh
```

验证在临时 Git index 内确认 patch apply/reverse 精确，不修改传入 checkout。构建使用冻结的 archive、patch、manifest 和独立 `nextcloud-auto-space-candidate` 镜像标签；共享运行栈的选择与配置由后续部署流程处理。

本机小型隔离 Nextcloud、AD、数据库和网络保留供复验；100 GB 压力原件及本任务全部 Go 测试容器均已清理。复验脚本只使用显式隔离 fixture，不能直接传入生产凭据。
