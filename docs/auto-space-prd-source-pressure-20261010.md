# 隔离 Nextcloud 原件压力测试

脚本：`scripts/ops/isolated-source-pressure.py`。此项验收覆盖实际 WebDAV 原件上传、物理原件容量、带签名的完整 manifest 和原件下载。100 GB 指十进制 `100,000,000,000` 字节，即 10,000 个实际 10,000,000 字节文件。

测试复用本次任务已经存在的 Nextcloud 34.0.4 / PostgreSQL 16 环境及 `python:3.12-alpine` 镜像。Nextcloud 容器与网络必须有 `codex.task=auto-space-prd-20261010` 标签，且容器不能公布宿主端口；镜像不存在时脚本失败。原件使用 `secrets.token_bytes(10_000_000)` 物化的随机缓冲区，通过 8 个并发 HTTP WebDAV PUT 请求写入。每次请求实际发送全部字节，不创建稀疏文件，不使用克隆，也不替换 filecache 元数据。

```sh
export WEKNORA_PRD_TEST_RUNTIME=1
export WEKNORA_PRD_PRESSURE_PASSWORD='已配置的隔离 synthetic 密码'
python3 scripts/ops/isolated-source-pressure.py \
  --workers 8 --artifacts /tmp/weknora-prd-real-source-pressure-full-20261010
```

人工再次运行应选择新的 artifacts 目录。脚本创建随机且唯一的 `prd-source-pressure-<32hex>` 目录；MKCOL 不是 201 时拒绝继续。每次客户端的资源上限为 1 CPU / 512 MiB。2026-10-10 全量运行的 upload 客户端在运行过程中补上同样上限；已有 Nextcloud 服务未额外设置容器资源上限。

所有 publisher 密码和机器令牌只写入权限为 700 的临时目录内、权限为 600 的文件，清理时删除；不写进此仓库或打印到报告。manifest 与 receipt 不含机器凭据。物理容量检查从 Nextcloud 本地存储路径的实际 `stat.size`、`stat.blocks * 512` 汇总，并要求文件数、原件字节数精确相等，分配字节数至少等于原件字节数。

绑定和机器 scoped key 创建后，客户端按同一 generation 翻完所有 manifest 页面；200 个文件一页，10,000 个文件应为 50 页。随后按 manifest 的 filecache ID、ETag、content revision 发出带 HMAC 与 If-Match 的 GET，将响应流入 SHA-256。每个文件必须返回精确字节数、匹配的 ETag / revision 响应头和相同的原件摘要。下载按 100 个文件的批次 drain，可在 artifacts 中创建 `pause-download` 令后续批次等待；移除此文件恢复，不中断正在读取的文件。报告分别记录总 wall time、协调等待和活跃时间。

本轮普通上传探针使用同一 marker 下独立的 `-normal-upload-probe` 目录，100 次单并发、每次 100,000 字节 PUT，间隔 200 ms，客户端上限 1 CPU / 128 MiB。探针不会进入 10,000 文件源目录，且已通过 HTTP DELETE 及精确回收站永久删除清理。负载中观测：100/100 成功、失败率 0、wall 140.992 秒（含间隔）、P50 1.091 秒、P95 2.315 秒。没有同条件压力前 baseline，不能将此观测解释为性能变化的因果结论。

脚本 finally 只停止并移除本次随机 marker 的 binding、机器 key 和原件。Nextcloud 原生删除会进入回收站，因此还按精确 owner、目录名和删除时间调用 Trashbin 永久删除，并确认对应回收站元数据及物理路径为空；不会清空整个用户的回收站。

最终实际全量结果和清理证明由 `receipt.json` 记录。本项不覆盖 LLM 解析 100 GB 文本、向量索引容量、全文索引容量或完整大文档图谱构建；这些验收必须独立执行并注明模型及索引环境。

本轮真实结果保存在 [原始事实及清理 receipt](evidence/source-pressure-20261010/receipt.json)。

| 项目 | 实际结果 |
| --- | --- |
| WebDAV PUT | 10,000/10,000 成功，100,000,000,000 字节，失败率 0 |
| 上传 wall / 吞吐 | 1,370.051 秒 / 72.990 MB/s |
| 上传 P50 / P95 | 0.969 / 2.073 秒 |
| 物理原件 | 10,000 文件，stat.size 总和 100,000,000,000，stat.blocks 分配 100,024,320,000 字节 |
| 签名 manifest | 50 页，10,000 唯一 ID，全部 size/ETag/revision 有效，同 generation；8.818 秒 |
| 签名下载及摘要 | 9,992 文件 / 99,920,000,000 字节精确摘要通过，8 次 RemoteDisconnected，失败率 0.08% |
| 下载活跃时间 / 吞吐 | 280.047 秒 / 356.798 MB/s（按成功核验字节计算） |
| 下载 P50 / P95 | 0.201 / 0.325 秒 |
| 下载 wall / 协调等待 | 9,436.862 / 9,156.816 秒；两个等待阶段未进行大原件读取 |
| 客户端峰值 RSS | 上传 56,483,840 / 下载 56,729,600 字节 |
| 清理 | binding/key 撤销，精确原件路径为空，精确 1 个 trash 根永久删除，trash 元数据和物理路径均为空 |

8 次失败发生在长时间协调等待后的首批请求，类型为 HTTP client `RemoteDisconnected`；过期的空闲 keep-alive 连接是推断原因，未收到 Nextcloud HTTP 失败状态。旧运行脚本随后已完成 binding/key 移除和原件删除，因此不能在原 head 上补验，也没有将其改写成 10,000 个摘要全部通过。此项本轮**未完成 10,000/10,000 全部字节下载校验**。

后续脚本已修复安全 GET 的一次重连：重建连接、重新生成签名 nonce，保留首轮传输错误与重试结果，最终仍须验证 ETag/revision、长度和 SHA-256。真实 loopback HTTP 断连接回归 `scripts/ops/test_isolated_source_pressure_transport.py` 已通过；本轮 100 GB 测试未重跑。Nextcloud cgroup 的历史峰值为 4,521,508,864 字节，包含文件缓存和此前同 owned 容器的 fixture，不能当作本项独立 RSS 峰值。
