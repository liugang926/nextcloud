# 生产部署：cloud.tianjizn.com

服务器先确认 CPU/内存/磁盘、已有 AI 容器、端口和 cgroup 预算，再部署独立 `nextcloud-production` Compose 项目。默认后端为 `http://10.106.1.128:18082`；用户自己的 HTTPS 代理负责 `https://cloud.tianjizn.com`，须保留 Host、转发 `/` 与 Office 的 `/hosting`、`/browser`、`/cool`（含 WebSocket）。宿主 nginx 不需要修改。

## 初次安装与开机启动

需要 Linux amd64、Docker Engine + Compose、Python 3、curl、tar、flock、systemd；无须 GitHub runner 或 GitHub 写入凭据。`install.sh` 创建缺失的随机 DB/管理员密码，已有秘密不覆盖，也不会输出密码。

```sh
sudo bash deploy/production/install.sh
sudo systemctl start nextcloud-production-update.service
sudo systemctl status nextcloud-production.service nextcloud-production-update.timer
```

`nextcloud-production.service` 与 Docker 随系统启动，10 个容器均设置 restart 策略。独立 slice 限制整个 Nextcloud 项目的 CPU/内存，避免挤占 AI 模型。开机前先检查/恢复中断的事务；存在需要人工恢复的事务时，不自动开放生产入口。

| 路径 | 用途 |
| --- | --- |
| `/srv/nextcloud-production/current` | 当前版本的原子符号链接 |
| `/srv/nextcloud-production/releases/<prod-tag>` | 各版本源码、manifest 和部署脚本 |
| `/srv/nextcloud-production/secrets/` | 文件秘密与 `images.env`，父目录仅 root 可进入 |
| `/srv/nextcloud-production/state/` | 版本账本、事务日志和互斥锁 |
| `/data/nextcloud-production/{html,data,postgres,redis,office}` | 持久数据 |
| `/data/nextcloud-production/backups/` | 更新前 DB、config、custom_apps 检查点 |

管理员默认 `admin`，密码保存在 `secrets/nextcloud_admin_password`；数据库密码保存在 `secrets/nextcloud_db_password`。需要以 root 在服务器读取并安全交接。不要把这些文件提交 Git。`images.env` 可保留 `NC_ADMIN_USER`、`NEXTCLOUD_DOMAIN`、`WEKNORA_HTTPS_ORIGINS` 等配置，自动更新只替换 5 个镜像变量。LDAP 连接和 WeKnora 配对需要管理员根据实际目录/应用配置。

## 确认合并后发布

先让变更进入 GitHub `main`，等待该**精确 commit** 的主分支 `integration.yml` 中 `local-smoke`（Nextcloud 安装/API）与 `direct-upgrade`（升级/恢复）两项成功，再创建新的 `prod-*` tag：

```sh
git switch main
git pull --ff-only
git tag prod-20261018-01
git push origin prod-20261018-01
```

该 tag 工作流检查 commit 是 `origin/main` 的祖先，并要求相同 SHA 的 main push 中上述两项 Nextcloud 检查成功，以及发布器安全自测成功；不等待本次未部署的历史 WeKnora 补丁矩阵，不假设仓库已经配置分支保护。工作流串行发布，不取消正在进行的生产发布。普通 main push、PR 或其他 tag 均不会发布生产版本。发布后不要移动 tag、替换 Release 资产或复用失败 tag；修改后使用新的 `prod-*` tag。

Actions 在 Linux amd64 拉取固定版本的 Nextcloud 34.0.4、PostgreSQL 16、Redis 7、Collabora CODE 26.04.2.4.1 和 nginx 1.30.3，记录真实 registry digest、image config ID 和架构；实际启动 Office，核对运行工具、HTTP discovery 和公开 HTTPS 地址后，打包运行源码、Nextcloud 34 兼容的 richdocuments 11.1.2、镜像及 SHA256 清单。镜像 gzip 超过约 1.7 GiB 时分片。所有资产上传至 draft 后才发布，避免服务器读到不完整版本。

当前构建要求 CI 使用 classic Docker 镜像存储来取得 config ID；若 inspect 显式返回 OCI Descriptor，构建立即停止，避免把运行 manifest/index 摘要误写为 config digest。生产服务器可使用下述已验证的 Docker 29 containerd 兼容层。

默认每约 2 分钟只读公开 GitHub Release；10.106.1.128 当前通过独立 `production-polling.conf` timer drop-in 调为 5 分钟，开机后约 90 秒首检，为同机服务保留共享 GitHub API 配额。该仓库 **Release 发布/资产修改权限就是生产部署权限**；SHA256 校验用于完整性校验，不是独立的代码签名或 workflow attestation。服务器不保存 GitHub token。部署前还验证 tag 的真实 commit/main 祖先关系、已运行版本 commit 的祖先关系、repo/tag/commit、清单、解压路径、镜像 ID 与 amd64 架构。Compose 使用内容寻址 image ID 且禁止隐式 pull，因此导入的镜像可以离线启动。

插件发布的 5 个镜像内容 ID 若已全部存在于服务器，并逐一验证为 Linux amd64，则复用这些镜像，跳过镜像包下载、拼接与导入。缺少任一镜像时下载并导入完整镜像包；Docker 库存/inspect 查询失败或已存在镜像架构错误时停止部署。两条路径都检查 Release 附件库存、来源、大小与清单，下载并校验部署源码 SHA256，并在激活前再次验证全部镜像 ID/架构。

发布 manifest 和版本账本始终保存 CI 的 **config digest**。classic Docker 直接以该摘要运行；Docker 29 containerd 存储的运行 ID 可能是 OCI manifest/index digest。兼容层仅从固定 Docker containerd socket 的 `moby` namespace 只读获取内容，核对 manifest/index 字节 SHA256、descriptor 大小、config digest 与 config 字节 SHA256，并验证 Linux amd64；确认后才将实际 runtime ID 写入 `images.env`。tag 只定位候选，不能作为身份凭据。config 不匹配的完整验证候选视为缺失并重新导入；损坏内容、读取失败或平台错误均停止部署。classic 存储中不同 config 的 tag 若无法建立 OCI 证明，也保守停止。

OCI 内容读取使用 `/usr/bin/ctr`，仅支持 `/run/containerd/containerd.sock` 与 `/var/run/docker/containerd/containerd.sock`，每个 blob 上限 4 MiB/30 秒，index 上限 16 个 descriptor、2 层嵌套且只允许唯一 Linux amd64 选择。无需改变 Docker 存储后端或重启 Docker。安装器先安装身份验证模块再安装调用方；旧发布包缺少新模块时保留服务器较新的完整发布器，避免切换应用版本时降级兼容层。

## 更新与恢复边界

更新通过 `flock` 串行执行：维护模式 → 关闭代理 → 优雅停止 Office/web/任务并排空写入 → `pg_dump` 与 config/custom_apps 检查点 → 原子切换版本与镜像配置 → 强制重建 bind mount → 迁移/内部健康检查。验证期间入口及 cron/四个 worker 保持关闭。

内部检查成功前失败，会恢复旧源码、升级前 DB 和 config/custom_apps，再验证旧服务。内部检查通过后先记录不可回退边界，再启动任务与生产入口；此后若失败，保留新 DB/文件并要求人工修复，避免覆盖已经接受的用户写入或 WeKnora 事件。只有完整健康检查通过才记录 `success`。检查点、失败版本与事务证据不会自动删除。

自动发布只更新本仓库插件，Nextcloud 核心固定为 34.0.4，且已部署核心 image ID 不允许自动改变。核心/大版本升级需要另行规划，脚本不会自行降级 Nextcloud 核心、删除用户数据或运行 `docker compose down -v`。DB/config 检查点用于部署恢复，灾备还需要另外备份用户 `data`、持久卷及秘密。

大于 32 MiB 的附件最多使用 8 路、4 MiB 分块下载，严格核对 HTTP 206、Content-Range、HTTPS 来源和最终 SHA256。完整分块留在受保护的 `state/asset-cache`，网络中断后可恢复；成功部署后清理该版本的下载缓存。下载网络错误最多尝试 3 次；验证失败或部署失败不会对同 tag 无限重试。人工手工建立的 `current` 必须包含有效 `manifest.json`，否则拒绝自动接管。事务进入 `manual-recovery` 后会阻止后续自动部署；先根据受保护的 `state/transaction.json`、`release-state.json` 与对应备份调查，确认实际源码、DB 和配置一致后再由管理员解除阻断，避免直接删日志盲目重跑。

```sh
# 查看发布状态和日志（不输出秘密）
sudo systemctl list-timers nextcloud-production-update.timer
sudo journalctl -u nextcloud-production-update.service -n 100 --no-pager
sudo cat /srv/nextcloud-production/state/release-state.json

# 手工一致性检查点；会短暂停止入口和任务，完成后验证并恢复
sudo /srv/nextcloud-production/bin/backup.sh /data/nextcloud-production/backups/manual-20261018-01

# 健康检查；外部 HTTPS 由用户代理单独验收
sudo /srv/nextcloud-production/current/deploy/production/healthcheck.sh
```

发布器安全测试：`python3 deploy/production/test_release.py`、`python3 deploy/production/test_image_identity.py`。测试覆盖恶意归档、classic/OCI 身份链、descriptor/hash/平台错误、manifest/核心变更、防降级、tag 重写、网络重试、开机中断恢复以及开放流量后禁止回滚 DB；main/PR CI 与生产发布均执行这些检查。
