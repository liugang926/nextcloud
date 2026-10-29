import { getSidebar } from '@nextcloud/files'

const TAG_NAME = 'integration_weknora-files-sidebar-tab'
const ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M4 3h11l5 5v13H4V3zm2 2v14h12V9h-4V5H6zm2 7h8v2H8v-2zm0 3h6v2H8v-2z"/></svg>'

class WeknoraFileSidebarTab extends HTMLElement {
    constructor() {
        super()
        this._node = null
        this._active = false
        this._request = 0
        this._folderSummary = null
        this._targetMigrations = new Map()
        this._activityCursor = null
    }

    connectedCallback() {
        this.renderMessage('正在读取文件发布状态…')
        this.load()
    }

    set node(value) {
        this._node = value
        this.load()
    }

    get node() {
        return this._node
    }

    set active(value) {
        this._active = Boolean(value)
        if (this._active) {
            this.load()
        }
    }

    get active() {
        return this._active
    }

    async load() {
        if (!this.isConnected || !this._active) {
            return
        }
        const fileId = String(this._node?.fileid ?? this._node?.attributes?.fileid ?? '')
        if (!/^[1-9][0-9]*$/.test(fileId)) {
            this.renderMessage('请选择一个文件。')
            return
        }

        const request = ++this._request
        this.renderMessage(this.isFolder() ? '正在读取目录状态…' : '正在读取文件发布状态…')
        try {
            const url = window.OC.generateUrl(`/apps/integration_weknora/api/v1/${this.isFolder() ? 'folders' : 'files'}/${encodeURIComponent(fileId)}/status`)
            const response = await fetch(url, {
                credentials: 'same-origin',
                cache: 'no-store',
                headers: {
                    Accept: 'application/json',
                    'X-Requested-With': 'XMLHttpRequest',
                    requesttoken: window.OC.requestToken || '',
                },
            })
            if (request !== this._request || !this.isConnected) {
                return
            }
            if (!response.ok) {
                this.renderMessage(response.status === 404
                    ? '这个文件目前不可访问。'
                    : '暂时无法确认发布状态，请稍后重试。')
                return
            }
            const data = await response.json()
            const activityKind = this.isFolder() ? 'folders' : 'files'
            try {
                data.activity = await this.api(
                    `/${activityKind}/${encodeURIComponent(fileId)}/activity?limit=5`)
                this._activityCursor = data.activity.next_cursor || null
            } catch (_) {
                data.activity = { items: [], next_cursor: null, unavailable: true }
                this._activityCursor = null
            }
            const pendingMigration = this._targetMigrations.get(fileId)
            if (this.isFolder() && data.manageable && pendingMigration) {
                try {
                    const status = await this.api(`/folders/${fileId}/target-migrations/${pendingMigration.operationId}`)
                    data.target_migration = status.migration
                    pendingMigration.confirmed = true
                    if (status.migration?.state === 'active') this._targetMigrations.delete(fileId)
                } catch (_) {
                    data.target_migration = { operation_id: pendingMigration.operationId, state: 'unverified' }
                }
            }
            if (request !== this._request || !this.isConnected) {
                return
            }
            if (this.isFolder()) {
                this._folderSummary = data.summary || null
                this.renderFolder(data)
            }
            else this.renderStatus(data)
        } catch (_) {
            if (request === this._request && this.isConnected) {
                this.renderMessage('暂时无法确认发布状态，请稍后重试。')
            }
        }
    }

    isFolder() {
        return ['folder', 'directory'].includes(this._node?.type)
    }

    nodeId() {
        return String(this._node?.fileid ?? this._node?.attributes?.fileid ?? '')
    }

    async api(path, method = 'GET', body) {
        const response = await fetch(window.OC.generateUrl(`/apps/integration_weknora/api/v1${path}`), {
            method, credentials: 'same-origin', cache: 'no-store',
            headers: { Accept: 'application/json', 'Content-Type': 'application/json',
                'X-Requested-With': 'XMLHttpRequest', requesttoken: window.OC.requestToken || '' },
            body: body === undefined ? undefined : JSON.stringify(body),
        })
        const data = await response.json()
        if (!response.ok) {
            const error = new Error(data.reason || data.error || 'directory_unavailable')
            error.code = data.error
            error.reasonCode = data.reason_code || data.error
            error.nextAction = data.next_action
            error.status = response.status
            throw error
        }
        return data
    }

    renderFolder(data) {
        const section = document.createElement('section')
        section.className = 'weknora-sidebar'
        section.setAttribute('aria-live', 'polite')
        const heading = document.createElement('h3')
        heading.textContent = 'WeKnora 目录'
        section.append(heading)
        const connected = data.pairing_state === 'active'
            && (!data.operation || data.operation.state === 'active')
        const targetDeleted = data.operation?.state === 'target_deleted' || data.source_state === 'target_deleted'
        const overview = {
            unbound: '未绑定', binding: '绑定中', binding_failed: '绑定失败', synchronizing: '同步中',
            processing: '内容处理中', ready: '已索引，可验证问答权限', partial: '部分可用',
            failed: '处理失败', sync_paused: '内容同步暂停', publication_stopped: '发布已停止',
            unverified: '待验证', event_delivery_error: '事件投递失败',
        }[data.overview_state] || '待验证'
        this.addRow(section, '目录状态', targetDeleted ? '目标已删除' : overview)
        if (data.directory_role) this.addRow(section, '目录类型', {
            associated_root: '已关联根目录', inherited_child: '继承子目录',
            unassociated_root: '可关联目录', new_directory_parent: '可新建关联目录',
            unmanageable: '不可管理',
        }[data.directory_role] || '不可管理')
        this.addRow(section, '关联状态', targetDeleted ? '目标知识库或工作空间已删除' : data.inherited ? '继承发布根的知识库'
            : data.knowledge_base_id && connected ? '已关联知识库'
                : data.operation || data.knowledge_base_id ? '待连接' : '未关联')
        if (data.binding_name) this.addRow(section, '发布目录', data.binding_name)
        if (data.space_name) this.addRow(section, '空间', data.space_name)
        if (data.knowledge_base_name || data.knowledge_base_id) {
            this.addRow(section, '知识库', data.knowledge_base_name || '已关联知识库')
        }
        if (data.publication_state) this.addRow(section, '发布', data.publication_state === 'active'
            ? connected ? '发布有效' : '待连接，发布待验证' : '已停止发布')
        if (typeof data.sync_enabled === 'boolean') this.addRow(section, '同步', data.sync_enabled ? '自动同步' : '已暂停同步')
        if (data.content_sync_state) this.addRow(section, '内容同步', {
            not_started: '尚未开始', queued: '已入队', delivering: '事件投递中',
            received: '已接收，等待执行', manifest_fetching: '正在核对完整清单',
            processing: '解析/索引处理中', ready: '当前版本已索引', partial: '部分文件可用',
            failed: '处理失败', paused: '内容同步暂停', unverified: '待重新验证',
            publication_stopped: '发布已停止', event_receiver_unauthorized: '事件接收凭据失效',
            waiting_manifest: '等待完整清单验证',
        }[data.content_sync_state] || '待验证')
        if (data.summary) {
            this.addRow(section, '当前可见文件', String(data.summary.total_visible ?? 0))
            this.addRow(section, '当前版本已完成', String(data.summary.current_completed ?? 0))
            const counts = data.summary.knowledge_counts || {}
            this.addRow(section, '处理统计', `已解析 ${counts.ready || 0}；处理中 ${counts.updating || 0}；失败 ${counts.failed || 0}；跳过 ${counts.skipped || 0}；待验证 ${counts.unverified || 0}`)
            if (data.summary.pipeline_counts) {
                const pipeline = data.summary.pipeline_counts
                this.addRow(section, '同步统计', `已导入 ${pipeline.imported || 0}；解析失败 ${pipeline.parse_failed || 0}；索引完成 ${pipeline.index_completed || 0}；跳过 ${pipeline.skipped || 0}`)
            }
            this.addRow(section, '图谱结果', Object.entries(data.summary.graph_counts || {})
                .filter(([, count]) => count > 0)
                .map(([state, count]) => `${this.graphLabel(state)} ${count}`).join('；') || '暂无文件')
            this.addRow(section, '最后验证时间', this.formatTime(data.summary.last_verified_at))
            this.addRow(section, '最近同步活动', this.formatTime(data.summary.last_sync_at))
            this.addRow(section, '最近解析成功', this.formatTime(data.summary.last_parse_success_at))
            this.addRow(section, '最近索引成功', this.formatTime(data.summary.last_index_success_at))
            if (data.summary.status_stale) this.addRow(section, '状态验证', '需要刷新验证')
            if (data.summary.partial) this.addRow(section, '统计范围', '当前页可见文件，更多文件待加载')
            if (Array.isArray(data.summary.failures) && data.summary.failures.length) {
                const details = document.createElement('details')
                details.className = 'weknora-sidebar__processing-details'
                const summary = document.createElement('summary')
                summary.textContent = `失败文件（${data.summary.failures.length}）`
                details.append(summary)
                for (const failure of data.summary.failures) {
                    this.addRow(details, failure.name || '一个文件',
                        this.failureText(failure.error_code || failure.knowledge_state))
                }
                section.append(details)
            }
            if (data.summary.next_cursor) this.button(section, '加载更多汇总', () => this.loadMoreSummary(data))
        }
        if (data.content_sync) {
            this.addRow(section, '事件水位', `已接收 ${data.content_sync.received_through || 0}；已执行 ${data.content_sync.applied_through || 0}`)
            if (data.content_sync.last_updated_at) this.addRow(section, '链路更新时间', this.formatTime(data.content_sync.last_updated_at))
            if (data.content_sync.next_retry_at) this.addRow(section, '下次重试', this.formatTime(data.content_sync.next_retry_at))
            if (data.content_sync.error_code) {
                this.addRow(section, '同步原因', this.failureText(data.content_sync.error_code))
            }
        }
        if (data.operation?.operation_id) this.addRow(section, '操作标识', String(data.operation.operation_id).slice(0, 8))
        if (targetDeleted) {
            this.addRow(section, '处理状态', '发布与自动同步已停止，来源记录和原文件已保留')
            this.addRow(section, '清理状态', '来源退出与索引清理仍需独立核验')
        } else if (data.inherited) {
            this.addRow(section, '配置', '子目录沿用发布根配置')
        } else if (data.manageable && data.target_migration && data.target_migration.state !== 'active') {
            this.addRow(section, '知识库迁移', {
                prepared: '已保存，旧目标读取已暂停', reserved: '新目标已预留', building: '新目标正在核验',
                unverified: '迁移结果待验证',
            }[data.target_migration.state] || '等待迁移完成')
            this.button(section, '重试同一迁移', () => this.retryTargetMigration(this.nodeId()))
        } else if (data.manageable && data.operation) {
            if (data.operation.state !== 'active') {
                const failure = {
                    unsupported_source_acl: '当前挂载或分享权限模式不受支持',
                    permission_or_target_conflict: '管理委派、目录范围或目标知识库冲突',
                    target_reservation_unverified: 'WeKnora 目标空间或知识库预留尚未验证',
                    target_status_unverified: '无法从 WeKnora 读取当前知识库目标状态',
                    target_status_server_error: 'WeKnora 读取知识库目标状态时发生内部错误',
                    target_status_server_error_onboarding: 'WeKnora 目录接入状态不可用',
                    target_status_server_error_onboarding_operation_read: 'WeKnora 无法读取目录接入操作',
                    target_status_server_error_onboarding_tenant_deletion: 'WeKnora 无法验证空间删除状态',
                    target_status_server_error_onboarding_target_observation: 'WeKnora 无法验证目标知识库状态',
                    target_status_server_error_onboarding_operation_refresh: 'WeKnora 无法刷新目录接入操作',
                    target_status_rate_limited: 'WeKnora 目标状态查询请求过多',
                    target_status_http_error: 'WeKnora 目标状态查询被拒绝',
                    target_status_rejected_onboarding_conflict: 'WeKnora 目录接入状态冲突',
                    target_status_rejected_authorization_denied: 'WeKnora 拒绝了目录状态查询身份',
                    target_status_rejected_authorization_denied_operation_read: 'WeKnora 拒绝读取目录接入操作',
                    target_status_rejected_authorization_denied_tenant_deletion: 'WeKnora 拒绝验证空间删除状态',
                    target_status_rejected_authorization_denied_target_observation: 'WeKnora 拒绝验证目标知识库状态',
                    target_status_rejected_authorization_denied_operation_refresh: 'WeKnora 拒绝刷新目录接入操作',
                    target_status_rejected_operation_missing: 'WeKnora 中找不到原目录接入操作',
                    target_status_rejected_target_closed: 'WeKnora 目标知识库已关闭',
                    pairing_prepare_unverified: 'Nextcloud 尚未准备好知识库配对凭据',
                    publication_stopped: '发布已停止，当前版本不可读取；点击“重试同一关联”进行安全恢复',
                    pairing_prepare_conflict: 'Nextcloud 中的配对凭据或目录状态发生冲突',
                    pairing_remote_unverified: 'WeKnora 知识库来源配对尚未验证',
                    pairing_remote_transport: '无法连接 WeKnora 配对服务',
                    pairing_remote_invalid_response: 'WeKnora 配对服务返回了无法识别的响应',
                    pairing_remote_server_error: 'WeKnora 配对服务内部错误',
                    pairing_remote_server_error_source_prepare: 'WeKnora 创建知识库来源失败',
                    pairing_remote_server_error_onboarding: 'WeKnora 配对状态或管理员用途保存失败',
                    pairing_remote_rate_limited: 'WeKnora 配对服务请求过多',
                    pairing_remote_http_error: 'WeKnora 配对服务拒绝了请求',
                    pairing_remote_credential_unavailable: 'Nextcloud 中保存的 WeKnora 连接凭据不可用',
                    pairing_remote_rejected_binding_unavailable: 'WeKnora 无法确认当前 Nextcloud 目录绑定',
                    pairing_remote_rejected_source_conflict: 'WeKnora 中的知识库来源与当前配对状态冲突',
                    pairing_remote_rejected_source_missing: 'WeKnora 中找不到原配对来源',
                    pairing_remote_rejected_onboarding_conflict: 'WeKnora 的目录接入状态发生冲突',
                    pairing_remote_rejected_target_closed: 'WeKnora 目标知识库已关闭',
                    pairing_remote_rejected_authorization_denied: 'WeKnora 拒绝了当前目录管理身份',
                    pairing_remote_rejected_operation_missing: 'WeKnora 中找不到原接入操作',
                    pairing_remote_rejected: 'WeKnora 拒绝了目录配对请求',
                    pairing_remote_rejected_http_400: 'WeKnora 拒绝了格式不完整的目录配对请求',
                    pairing_remote_rejected_http_401: 'WeKnora 配对凭据已失效',
                    pairing_remote_rejected_http_403: 'WeKnora 拒绝了当前目录管理身份',
                    pairing_remote_rejected_http_404: 'WeKnora 中找不到原接入操作',
                    pairing_remote_rejected_http_409: 'WeKnora 的目录或知识库来源状态冲突',
                    pairing_remote_rejected_http_422: 'WeKnora 拒绝了目录配对参数',
                    pairing_ack_unverified: '知识库配对确认尚未保存到 Nextcloud',
                    pairing_ack_conflict: '知识库配对确认与 Nextcloud 本地状态冲突',
                    event_connection_conflict: 'WeKnora 事件连接与本地绑定状态冲突',
                    event_connection_pending: '知识库已创建，但 WeKnora 事件连接尚未建立',
                    grant_snapshot_unavailable: 'Nextcloud 目录权限快照无法生成',
                    grant_sync_unverified: 'WeKnora 目录权限同步尚未验证',
                    permission_refresh_transport: '暂时无法连接 WeKnora，目录权限同步将自动重试',
                    permission_refresh_invalid_response: 'WeKnora 返回了无法验证的目录权限响应，将自动重试',
                    permission_refresh_server_error: 'WeKnora 目录权限服务暂时不可用，将自动重试',
                    permission_refresh_rate_limited: 'WeKnora 目录权限服务请求过多，将自动重试',
                    permission_refresh_credential_unavailable: 'Nextcloud 保存的 WeKnora 连接凭据暂时不可用，将自动重试',
                    permission_refresh_retry: '目录权限同步暂时失败，将自动重试',
                    reconcile_pending: '完整目录清单尚未加入同步队列',
                    connection_unverified: '连接结果尚未验证，原文件已保留',
                }[data.operation.error_code]
                const statusMatch = /^target_status_server_error_onboarding_http_(5[0-9]{2})$/
                    .exec(String(data.operation.error_code || ''))
                this.addRow(section, '连接原因', failure ||
                    (statusMatch ? `WeKnora 目录接入状态不可用（HTTP ${statusMatch[1]}）` : '等待关联完成'))
                this.button(section, '重试同一关联', async () => {
                    await this.api(`/directory-operations/${encodeURIComponent(data.operation.operation_id)}/retry`, 'POST', {})
                    this.load()
                })
            } else {
                if (data.permissions?.can_take_over_kb !== false) {
                    this.button(section, '更换知识库', () => this.renderAssociation(false, true))
                }
                if (data.sync_enabled) {
                    this.button(section, '立即同步', async () => {
                        await this.api(`/folders/${this.nodeId()}/sync-now`, 'POST', {})
                        this.load()
                    })
                }
                this.button(section, data.sync_enabled ? '暂停同步' : '恢复同步', async () => {
                    await this.api(`/folders/${this.nodeId()}/sync`, 'POST', {
                        sync_enabled: !data.sync_enabled, expected_revision: data.sync_revision,
                    })
                    this.load()
                })
            }
        } else if (data.manageable || (data.can_bind && !data.binding_id)) {
            this.button(section, '关联知识库', () => this.renderAssociation(false))
        }
        if (data.can_create) {
            this.button(section, '新建关联目录', () => this.renderAssociation(true))
        }
        if (data.knowledge_base_url) {
            const link = document.createElement('a')
            link.className = 'weknora-sidebar__link'
            link.href = data.knowledge_base_url
            link.target = '_blank'
            link.rel = 'noopener noreferrer'
            link.textContent = '打开知识库'
            section.append(link)
        }
        this.renderActivity(section, data.activity)
        if (data.activity?.unavailable) {
            this.addRow(section, '最近活动', '暂时无法读取，保留当前状态结果')
        }
        this.button(section, '刷新状态', () => this.load())
        this.replaceChildren(section)
    }

    async loadMoreSummary(data) {
        const fileId = this.nodeId()
        const request = ++this._request
        const previous = this._folderSummary
        if (!previous?.next_cursor) return
        try {
            const next = await this.api(`/folders/${fileId}/status?cursor=${encodeURIComponent(previous.next_cursor)}`)
            if (request !== this._request || fileId !== this.nodeId() || !this.isConnected) return
            if (!next.summary || next.summary.generation !== previous.generation
                || next.summary.page_offset !== previous.next_offset) throw new Error('summary_generation_changed')
            const page = next.summary
            const merged = { ...page, page_offset: 0, total_visible: previous.total_visible + page.total_visible,
                current_completed: previous.current_completed + page.current_completed,
                status_stale: previous.status_stale || page.status_stale,
                last_verified_at: previous.last_verified_at && page.last_verified_at
                    ? Math.min(previous.last_verified_at, page.last_verified_at) : previous.last_verified_at || page.last_verified_at,
                failures: [...previous.failures, ...page.failures].slice(0, 20) }
            for (const field of ['last_sync_at', 'last_parse_success_at', 'last_index_success_at']) {
                merged[field] = Number.isSafeInteger(previous[field]) && Number.isSafeInteger(page[field])
                    ? Math.max(previous[field], page[field])
                    : (previous[field] || page[field] || null)
            }
            for (const field of ['knowledge_counts', 'graph_counts', 'pipeline_counts']) {
                if (!page[field]) continue
                merged[field] = { ...(previous[field] || {}) }
                for (const [state, count] of Object.entries(page[field])) merged[field][state] = (merged[field][state] || 0) + count
            }
            this._folderSummary = merged
            const updated = { ...data, ...next, summary: merged }
            updated.content_sync_state = this.contentStateFromSummary(updated)
            updated.overview_state = updated.publication_state === 'stopped'
                ? 'publication_stopped'
                : ({
                    ready: 'ready', partial: 'partial', failed: 'failed', processing: 'processing',
                    paused: 'sync_paused', unverified: 'unverified',
                }[updated.content_sync_state] || 'unverified')
            this.renderFolder(updated)
        } catch (error) {
            // Old page totals are immediately discarded on scope changes.
            this._folderSummary = null
            if (request === this._request && fileId === this.nodeId()) {
                if (error.message === 'summary_generation_changed') this.load()
                else this.renderMessage('汇总暂不可用，请刷新重新验证当前可见文件。')
            }
        }
    }

    button(parent, label, action) {
        const button = document.createElement('button')
        button.type = 'button'
        button.textContent = label
        button.addEventListener('click', async () => {
            button.disabled = true
            try {
                await action()
            } catch (error) {
                this.renderMessage(error.nextAction
                    ? `${error.message} ${error.nextAction}`
                    : '操作未完成。请检查管理资格与配置后刷新重试。')
            }
            finally { button.disabled = false }
        })
        parent.append(button)
        return button
    }

    contentStateFromSummary(data) {
        if (data.sync_enabled === false) return 'paused'
        const summary = data.summary || {}
        if (summary.partial) return 'unverified'
        const counts = summary.knowledge_counts || {}
        const ready = Number(counts.ready || 0)
        const failed = Number(counts.failed || 0)
        const updating = Number(counts.updating || 0)
        const unverified = Number(counts.unverified || 0)
        const skipped = Number(counts.skipped || 0)
        const total = Number(summary.total_visible || 0)
        if (failed > 0) return ready > 0 ? 'partial' : 'failed'
        if (updating > 0) return 'processing'
        if (ready + skipped === total && unverified === 0) return skipped > 0 ? 'partial' : 'ready'
        return 'unverified'
    }

    renderAssociationSpaceChooser(creating, migrating, result) {
        const form = document.createElement('form')
        form.className = 'weknora-sidebar weknora-sidebar__form'
        const heading = document.createElement('h3')
        heading.textContent = creating ? '新建目录并关联知识库' : migrating ? '迁移到新的知识库' : '关联知识库'
        form.append(heading)
        const explanation = document.createElement('p')
        explanation.textContent = '请先选择 WeKnora 空间。下一步只显示该空间下可用的知识库。'
        form.append(explanation)
        const spaces = (result.spaces || []).filter(space => space.available !== false && space.can_bind !== false)
        const space = document.createElement('select')
        space.append(new Option('请选择空间', ''))
        for (const candidate of spaces) {
            const option = document.createElement('option')
            option.value = String(candidate.tenant_id)
            option.textContent = candidate.space_name || candidate.name || `空间 ${candidate.tenant_id}`
            space.append(option)
        }
        this.field(form, 'WeKnora 空间', space)
        const next = document.createElement('button')
        next.type = 'submit'
        next.textContent = '下一步：选择知识库'
        next.disabled = true
        form.append(next)
        const reasons = document.createElement('div')
        reasons.className = 'weknora-sidebar__reasons'
        if (!spaces.length) {
            const note = document.createElement('p')
            note.className = 'weknora-sidebar__hint'
            note.textContent = result.reason_code === 'space_not_visible'
                ? '当前用户没有可见的 WeKnora 空间，请联系管理员调整空间可见范围。'
                : '当前没有可用的 WeKnora 空间。'
            reasons.append(note)
        }
        form.append(reasons)
        this.button(form, '取消', () => this.load())
        space.addEventListener('change', () => { next.disabled = space.value === '' })
        form.addEventListener('submit', (event) => {
            event.preventDefault()
            if (space.value === '') return
            this.renderAssociation(creating, migrating, '', space.value)
        })
        this.replaceChildren(form)
    }

    async renderAssociation(creating, migrating = false, query = '', spaceId = '') {
        const selectedId = this.nodeId()
        const request = ++this._request
        const params = new URLSearchParams({ for_creation: creating ? '1' : '0', limit: '50' })
        if (query) params.set('q', query)
        if (spaceId) params.set('tenant_id', spaceId)
        else params.set('catalog', '1')
        const result = await this.api(`/folders/${selectedId}/candidates?${params}`)
        if (request !== this._request || selectedId !== this.nodeId() || !this.isConnected) return
        if (!spaceId) {
            this.renderAssociationSpaceChooser(creating, migrating, result)
            return
        }
        const form = document.createElement('form')
        form.className = 'weknora-sidebar weknora-sidebar__form'
        const heading = document.createElement('h3')
        heading.textContent = creating ? '新建目录并关联知识库' : migrating ? '迁移到新的知识库' : '关联知识库'
        form.append(heading)
        if (migrating) {
            const explanation = document.createElement('p')
            explanation.textContent = '迁移会先暂停旧目标读取，再核验新的知识库。原文件与工作空间保留，完成前显示待连接。'
            form.append(explanation)
        }
        const fallbackTarget = { tenant_id: result.tenant_id,
            processing_template_id: result.processing_template_id, candidates: result.candidates || [],
            ineligible: result.ineligible || [], available: true, can_bind: true }
        const targets = Array.isArray(result.targets) && result.targets.length
            ? result.targets : [fallbackTarget]
        // A selected space is an isolation boundary. Never fall back to the
        // first target if an upstream response carries a different tenant.
        const spaces = targets.filter(candidate =>
            candidate && String(candidate.tenant_id || '') === String(spaceId))
        const search = document.createElement('div')
        search.className = 'weknora-sidebar__search'
        const searchInput = document.createElement('input')
        searchInput.type = 'search'
        searchInput.placeholder = '按知识库名称搜索'
        searchInput.value = query
        searchInput.setAttribute('aria-label', '按知识库名称搜索')
        const searchButton = document.createElement('button')
        searchButton.type = 'button'
        searchButton.textContent = '刷新候选'
        searchButton.addEventListener('click', () =>
            this.renderAssociation(creating, migrating, searchInput.value.trim(), spaceId))
        search.append(searchInput, searchButton)
        form.append(search)
        const firstUsable = spaces.find(candidate => candidate.available !== false && candidate.can_bind !== false)
        const selectedSpace = firstUsable || spaces[0]
        const selectedSpaceName = selectedSpace?.space_name || selectedSpace?.name || `空间 ${spaceId}`
        const selectedSpaceRow = document.createElement('div')
        selectedSpaceRow.className = 'weknora-sidebar__selected-space'
        selectedSpaceRow.append(document.createTextNode(`已选空间：${selectedSpaceName} `))
        this.button(selectedSpaceRow, '重新选择空间', () => this.renderAssociation(creating, migrating))
        form.append(selectedSpaceRow)
        const candidateProvenance = document.createElement('p')
        candidateProvenance.className = 'weknora-sidebar__hint'
        candidateProvenance.id = `weknora-kb-candidates-${selectedId}`
        candidateProvenance.textContent =
            `以下候选来自 WeKnora 空间“${selectedSpaceName}”。这里是 WeKnora 知识库，不是 Nextcloud 目录；` +
            '知识库名称可能与目录名称相同。'
        form.append(candidateProvenance)
        const folderName = document.createElement('input')
        folderName.type = 'text'; folderName.maxLength = 255; folderName.required = creating
        const folderField = this.field(form, 'Nextcloud 目录名称', folderName)
        folderField.hidden = !creating
        const mode = document.createElement('input')
        mode.type = 'hidden'
        mode.value = 'existing'
        form.append(mode)
        const target = document.createElement('select')
        target.setAttribute('aria-describedby', candidateProvenance.id)
        this.field(form, 'WeKnora 知识库', target)
        const reasons = document.createElement('div')
        reasons.className = 'weknora-sidebar__reasons'
        reasons.setAttribute('aria-live', 'polite')
        form.append(reasons)
        const loadMore = document.createElement('button')
        loadMore.type = 'button'; loadMore.textContent = '加载更多知识库'; loadMore.hidden = true
        form.append(loadMore)
        const selectedTarget = () => selectedSpace
        let selectedTemplate = ''
        const updateCandidates = () => {
            target.replaceChildren()
            const selected = selectedTarget()
            selectedTemplate = selected ? String(selected.processing_template_id) : ''
            for (const candidate of selected?.candidates || []) {
                const option = document.createElement('option')
                option.value = candidate.id
                option.dataset.source = 'weknora'
                option.dataset.knowledgeBaseId = candidate.id
                option.title = `WeKnora 知识库 ID：${candidate.id}`
                option.textContent = candidate.name
                target.append(option)
            }
            target.disabled = !selected || selected.available === false || !(selected.candidates || []).length
            reasons.replaceChildren()
            for (const candidate of selected?.ineligible || []) {
                const note = document.createElement('p')
                note.className = 'weknora-sidebar__hint'
                note.textContent = `${candidate.name || '知识库'}：${candidate.reason || candidate.reason_code || '配置未通过核验'}`
                if (candidate.next_action) note.textContent += ` ${candidate.next_action}`
                reasons.append(note)
            }
            if (!selected || selected.available === false) {
                const note = document.createElement('p')
                note.className = 'weknora-sidebar__hint'
                note.textContent = `当前空间不可用：${selected?.reason_code || result.reason_code || 'space_candidate_unavailable'}。请联系目录发布管理者。`
                reasons.append(note)
            } else if (!(selected.candidates || []).length) {
                const note = document.createElement('p')
                note.className = 'weknora-sidebar__hint'
                note.textContent = selected?.next_cursor
                    ? '本页没有符合条件的已有知识库，可加载更多；系统不会自动转为新建。'
                    : '当前 WeKnora 空间没有符合条件、可供关联的已有知识库。请先在 WeKnora 中完成知识库创建和配置。'
                reasons.append(note)
            }
            loadMore.hidden = !selected?.next_cursor
            updateMode()
        }
        const existingNote = document.createElement('p')
        existingNote.className = 'weknora-sidebar__hint'
        form.append(existingNote)
        const submit = document.createElement('button')
        submit.type = 'submit'; submit.textContent = creating ? '创建并关联' : migrating ? '开始迁移' : '关联'
        form.append(submit)
        const updateMode = () => {
            folderField.hidden = !creating
            folderName.required = creating
            target.required = true
            submit.disabled = !target.value || (creating && folderName.value.trim() === '')
            existingNote.textContent = '只选择 WeKnora 当前空间中的已有知识库；不会选择或改名 Nextcloud 目录。'
        }
        folderName.addEventListener('input', updateMode)
        target.addEventListener('change', updateMode)
        updateCandidates()
        loadMore.addEventListener('click', async () => {
            const selected = selectedTarget()
            if (!selected?.next_cursor) return
            loadMore.disabled = true
            try {
                const moreParams = new URLSearchParams({ for_creation: creating ? '1' : '0', limit: '50',
                    tenant_id: String(selected.tenant_id), q: query, cursor: selected.next_cursor })
                const page = await this.api(`/folders/${selectedId}/candidates?${moreParams}`)
                if (page.candidate_revision !== result.candidate_revision) throw new Error('candidate_page_changed')
                const nextTarget = (page.targets || []).find(candidate =>
                    String(candidate.tenant_id) === String(selected.tenant_id) &&
                    String(candidate.processing_template_id) === String(selected.processing_template_id))
                if (!nextTarget) throw new Error('candidate_page_changed')
                const ids = new Set((selected.candidates || []).map(candidate => candidate.id))
                selected.candidates = [...(selected.candidates || []),
                    ...(nextTarget.candidates || []).filter(candidate => !ids.has(candidate.id))]
                selected.ineligible = [...(selected.ineligible || []), ...(nextTarget.ineligible || [])]
                selected.next_cursor = nextTarget.next_cursor
                selected.candidate_revision = nextTarget.candidate_revision
                updateCandidates()
            } catch (_) {
                this.renderMessage('候选已变化或无法加载更多，请取消后刷新候选。')
            } finally { loadMore.disabled = false }
        })
        this.button(form, '取消', () => this.load())
        const operationId = crypto.randomUUID()
        let submittedChoice
        form.addEventListener('submit', async (event) => {
            event.preventDefault()
            if (!form.reportValidity()) return
            submit.disabled = true
            target.disabled = true; folderName.disabled = true
            const selected = selectedTarget()
            const chosen = selected?.candidates?.find(candidate => String(candidate.id) === target.value)
            submittedChoice ||= { operation_id: operationId, mode: 'existing',
                name: chosen?.name,
                knowledge_base_id: target.value,
                folder_name: creating ? folderName.value : undefined,
                tenant_id: String(selected?.tenant_id || spaceId), processing_template_id: selectedTemplate,
                target_candidate_revision: selected?.candidate_revision || null,
                candidate_revision: result.candidate_revision || null }
            if (migrating && !this._targetMigrations.has(selectedId)) {
                this._targetMigrations.set(selectedId, { operationId, choice: submittedChoice, confirmed: false })
            }
            try {
                const pending = migrating ? this._targetMigrations.get(selectedId) : null
                const path = pending?.confirmed ? `/folders/${selectedId}/target-migrations/${operationId}`
                    : `/folders/${selectedId}/${creating ? 'create' : migrating ? 'target-migration' : 'associate'}`
                const reply = await this.api(path, 'POST', pending?.confirmed ? {} : submittedChoice)
                if (selectedId !== this.nodeId()) return
                const shortId = String(reply.operation?.operation_id || reply.migration?.operation_id || operationId).slice(0, 8)
                if (migrating) {
                    const current = this._targetMigrations.get(selectedId)
                    if (current) current.confirmed = true
                    if (reply.migration?.state === 'active') this._targetMigrations.delete(selectedId)
                    this.load()
                } else if (creating) {
                    this.renderMessage(reply.operation.state === 'active'
                        ? `目录已创建并关联（操作 ${shortId}）。打开新目录的详情查看状态。`
                        : `目录已保留，关联正在重试（操作 ${shortId}）。`)
                    this.button(this.firstElementChild, '刷新文件列表', () => window.location.reload())
                } else {
                    this.renderMessage(reply.operation?.state === 'active'
                        ? `关联已完成（操作 ${shortId}）。正在刷新目录同步状态。`
                        : `关联已保存，后台正在处理（操作 ${shortId}）。`)
                    this.button(this.firstElementChild, '刷新状态', () => this.load())
                }
            } catch (error) {
                const message = document.createElement('p')
                const changed = error.reasonCode === 'candidate_revision_changed'
                message.textContent = changed
                    ? `${error.message} ${error.nextAction}`
                    : (error.nextAction
                        ? `${error.message} ${error.nextAction} 重试将继续同一操作。`
                        : '未能确认关联结果。重试将继续同一操作。')
                form.append(message)
                if (changed) {
                    submit.textContent = '候选已变化'; submit.disabled = true
                    this.button(form, '刷新候选', () => this.renderAssociation(creating, migrating, query, spaceId))
                } else {
                    submit.textContent = '重试同一操作'; submit.disabled = false
                }
            }
        })
        this.replaceChildren(form)
    }

    async retryTargetMigration(folderId) {
        const pending = this._targetMigrations.get(folderId)
        if (!pending) { this.load(); return }
        const path = pending.confirmed
            ? `/folders/${folderId}/target-migrations/${pending.operationId}`
            : `/folders/${folderId}/target-migration`
        const reply = await this.api(path, 'POST', pending.confirmed ? {} : pending.choice)
        pending.confirmed = true
        if (reply.migration?.state === 'active') this._targetMigrations.delete(folderId)
        if (folderId === this.nodeId()) this.load()
    }

    field(parent, title, input) {
        const label = document.createElement('label')
        label.append(document.createTextNode(title), input)
        parent.append(label)
        return label
    }

    graphLabel(state) {
        return { not_enabled: '未启用', not_provided: '未提供', unsupported: '不支持', skipped: '已跳过', blocked: '已阻断',
            pending: '待处理', running: '处理中', succeeded: '已完成', succeeded_empty: '完成且无实体',
            partial_failed: '部分失败', failed: '失败', cancelled: '已取消', superseded: '已被新版本替代',
            unverified: '待验证' }[state] || '待验证'
    }

    failureText(code) {
        if (!code) return '原因待验证'
        return {
            parse_failed: '文件解析失败，请检查文件格式或处理方案',
            chunk_failed: '分块失败，请联系管理员检查处理配置',
            embedding_failed: '嵌入生成失败，需要重试当前版本',
            index_failed: '索引构建失败，当前版本暂不可问答',
            no_retrievable_content: '当前版本没有可检索内容，请补充内容后重新保存',
            source_changed: '处理期间文件再次变化，已放弃旧版本',
            source_unavailable: '暂时无法读取源文件，未确认删除',
            publication_stopped: '发布已停止，来源内容不可读取',
            permission_unverified: '暂时无法确认当前访问权限，已按不可用处理',
            identity_unmapped: '当前用户没有有效的目录身份映射',
            template_unavailable: '知识库处理方案不可用',
            graph_extract_failed: '图谱抽取失败，处理详情中可查看影响',
            graph_persist_failed: '图谱保存失败，当前版本未开放',
            superseded: '任务已被更新的文件版本替代',
            unverified: '当前状态尚无法确认',
        }[code] || '处理未完成，请刷新状态或联系管理员查看记录'
    }

    cleanupLabel(state) {
        return {
            none: '无需回收',
            not_required: '无需回收',
            pending: '回收待开始',
            blocked: '回收受引用或租约阻断',
            running: '回收中',
            partial: '部分完成，等待重试',
            verified: '回收已验证',
            withdrawn: '知识已撤回',
            closed: '访问已关闭',
            access_closed: '访问已关闭',
            completed: '回收完成',
            cleanup_pending: '回收待开始',
            cleanup_blocked: '回收受引用或租约阻断',
            cleanup_partial: '部分完成，等待重试',
            cleanup_verified: '回收已验证',
            unverified: '回收状态待验证',
        }[state] || '待验证'
    }

    renderActivity(section, activity) {
        if (!activity || activity.unavailable || !Array.isArray(activity.items)) return
        const details = document.createElement('details')
        details.className = 'weknora-sidebar__activity'
        const summary = document.createElement('summary')
        summary.textContent = `最近活动（${activity.items.length}）`
        details.append(summary)
        const list = document.createElement('ol')
        list.className = 'weknora-sidebar__timeline'
        for (const item of activity.items) list.append(this.activityItem(item))
        details.append(list)
        if (activity.next_cursor) {
            const more = document.createElement('button')
            more.type = 'button'
            more.className = 'button'
            more.textContent = '加载更多活动'
            more.addEventListener('click', () => this.loadMoreActivity(list, more))
            details.append(more)
        } else if (!activity.items.length) {
            details.append(this.hint('暂无可用活动记录。'))
        }
        section.append(details)
    }

    activityItem(item) {
        const row = document.createElement('li')
        const time = document.createElement('time')
        time.dateTime = Number.isSafeInteger(item.occurred_at)
            ? new Date(item.occurred_at * 1000).toISOString() : ''
        time.textContent = this.formatTime(item.occurred_at)
        const title = document.createElement('strong')
        title.textContent = String(item.title || '状态更新')
        const body = document.createElement('p')
        body.textContent = [item.label, item.result].filter(Boolean).join('：')
        row.append(time, title, body)
        return row
    }

    async loadMoreActivity(list, button) {
        if (!this._activityCursor) return
        button.disabled = true
        try {
            const kind = this.isFolder() ? 'folders' : 'files'
            const page = await this.api(`/${kind}/${this.nodeId()}/activity?limit=10&cursor=${encodeURIComponent(this._activityCursor)}`)
            for (const item of page.items || []) list.append(this.activityItem(item))
            this._activityCursor = page.next_cursor || null
            button.hidden = !this._activityCursor
        } catch (_) {
            list.append(this.hint('活动分页暂时不可用，请刷新状态。'))
        } finally {
            button.disabled = false
        }
    }

    hint(value) {
        const note = document.createElement('p')
        note.className = 'weknora-sidebar__hint'
        note.textContent = value
        return note
    }

    renderMessage(message) {
        const section = document.createElement('section')
        section.className = 'weknora-sidebar'
        section.setAttribute('aria-live', 'polite')
        const text = document.createElement('p')
        text.textContent = message
        section.append(text)
        this.replaceChildren(section)
    }

    renderStatus(data) {
        if (!data || !['in_scope', 'withdrawn', 'publication_stopped', 'outside_scope'].includes(data.source_state)) {
            this.renderMessage('暂时无法确认发布状态，请稍后重试。')
            return
        }
        const section = document.createElement('section')
        section.className = 'weknora-sidebar'
        section.setAttribute('aria-live', 'polite')

        const heading = document.createElement('h3')
        heading.textContent = 'WeKnora 文件状态'
        section.append(heading)

        const state = document.createElement('p')
        state.className = 'weknora-sidebar__state'
        state.textContent = {
            in_scope: '已纳入发布范围',
            withdrawn: '已撤回发布',
            publication_stopped: '发布目录已停止发布',
            outside_scope: '不在发布目录中',
        }[data.source_state]
        section.append(state)

        if (data.source_state === 'in_scope' || data.source_state === 'withdrawn'
            || data.source_state === 'publication_stopped') {
            this.addRow(section, '发布目录', data.binding_name || '未提供')
        }
        if (data.space_name) this.addRow(section, '空间', data.space_name)
        else if (data.source_state !== 'outside_scope') this.addRow(section, '空间', '名称待验证')
        if (data.knowledge_base_name) this.addRow(section, '知识库', data.knowledge_base_name)
        else if (data.source_state !== 'outside_scope') this.addRow(section, '知识库', '名称待验证')
        this.addRow(section, '源文件更新时间', this.formatTime(data.source_modified_at))
        if (data.source_state === 'in_scope') {
            const knowledgeState = {
                ready: '当前版本已解析并发布',
                updating: '正在更新',
                failed: data.failure_code === 'no_retrievable_content'
                    ? '当前版本没有可检索内容' : '当前版本处理失败',
                unverified: '尚未验证',
            }[data.knowledge_state] || '尚未验证'
            this.addRow(section, '知识状态', knowledgeState)
        }
        this.addRow(section, '当前源版本', data.source_etag || '未知')
        this.addRow(section, '已发布源版本', data.published_source_etag || '尚未验证')
        if (data.source_etag && data.published_source_etag) {
            this.addRow(section, '版本一致性', data.source_etag === data.published_source_etag
                ? '当前版本已发布'
                : '当前文件已更新；旧版本已退出当前问答，等待新版本发布')
        }
        this.addRow(section, '知识就绪时间', this.formatTime(data.knowledge_ready_at))
        if (data.stages && typeof data.stages === 'object') {
            const labels = { parse: '解析', chunk: '分块', embedding: '嵌入', index: '索引' }
            for (const [stage, state] of Object.entries(data.stages)) {
                if (labels[stage]) {
                    this.addRow(section, labels[stage], this.graphLabel(typeof state === 'object' ? state.state : state))
                    if (state.completed_at) this.addRow(section, `${labels[stage]}完成时间`, this.formatTime(state.completed_at))
                }
            }
        }
        if (data.sync_state) this.addRow(section, '同步阶段', { discovered: '已发现', downloading: '下载中',
            ingested: '已接收', failed: '失败', withdrawn: '已撤回' }[data.sync_state] || '待验证')
        if (data.publication_state) this.addRow(section, '发布阶段', { published: '已发布', blocked: '已阻断',
            pending: '待发布', failed: '失败', withdrawn: '已撤回' }[data.publication_state] || '待验证')
        if (data.withdrawal_state && data.withdrawal_state !== 'none') {
            this.addRow(section, '知识撤回', this.cleanupLabel(data.withdrawal_state))
        }
        if (data.gc_state && data.gc_state !== 'none') {
            this.addRow(section, '知识副本回收', this.cleanupLabel(data.gc_state))
        }
        if (data.graph) {
            this.addRow(section, '文件图谱', this.graphLabel(data.graph.state))
            if (typeof data.graph.enabled === 'boolean') this.addRow(section, '图谱要求', !data.graph.enabled ? '未启用'
                : data.graph.required ? '图谱完成后发布' : '文档可独立发布')
            const graphDetails = document.createElement('details')
            graphDetails.className = 'weknora-sidebar__processing-details'
            const graphSummary = document.createElement('summary')
            graphSummary.textContent = '处理详情'
            graphDetails.append(graphSummary)
            for (const [field, label] of Object.entries({ expected: '预期', succeeded: '成功', failed: '失败',
                skipped: '跳过', cancelled: '取消', pending: '待处理' })) {
                if (Number.isSafeInteger(data.graph[field])) {
                    this.addRow(graphDetails, `图谱任务${label}`, String(data.graph[field]))
                }
            }
            if (data.graph.completed_at) {
                this.addRow(graphDetails, '图谱完成时间', this.formatTime(data.graph.completed_at))
            }
            if (graphDetails.querySelector('.weknora-sidebar__row')) section.append(graphDetails)
        }
        if (data.occurred_at) this.addRow(section, '状态更新时间', this.formatTime(data.occurred_at))
        if (data.last_verified_at) this.addRow(section, '最后验证时间', this.formatTime(data.last_verified_at))
        if (data.status_stale) {
            this.addRow(section, '状态验证', '待验证')
            if (data.last_verified_knowledge_state) {
                this.addRow(section, '上次验证结果', {
                    ready: '已解析并发布',
                    updating: '正在更新',
                    failed: '处理失败',
                    unverified: '待验证',
                }[data.last_verified_knowledge_state] || '待验证')
            }
        }
        if (data.failure_code) this.addRow(section, '失败原因', this.failureText(data.failure_code))

        const explanation = document.createElement('p')
        explanation.className = 'weknora-sidebar__explanation'
        if (data.source_state === 'in_scope') {
            explanation.textContent = {
                ready: 'WeKnora 已解析并发布此源文件的当前版本。能否提问仍取决于你在 WeKnora 登录后的个人权限。',
                updating: 'WeKnora 尚未发布此源文件的当前版本。请稍后刷新。',
                failed: data.failure_code === 'no_retrievable_content'
                    ? '此文件没有可供问答的文本内容。请添加内容后保存，或联系管理员检查解析配置。'
                    : 'WeKnora 处理此源文件的当前版本时失败。请联系管理员查看同步与解析记录。',
                unverified: '目前无法验证 WeKnora 是否已解析并发布当前版本。请稍后刷新。',
            }[data.knowledge_state] || '目前无法验证 WeKnora 是否已解析并发布当前版本。请稍后刷新。'
        } else if (data.source_state === 'withdrawn') {
            explanation.textContent = '管理员已撤回此文件的发布资格。原文件仍保留在 Nextcloud。'
        } else if (data.source_state === 'publication_stopped') {
            explanation.textContent = data.file_withdrawn
                ? '管理员已停止整个发布目录，此文件的单独撤回也仍然有效。原文件保留在 Nextcloud。'
                : '管理员已停止整个发布目录。原文件保留在 Nextcloud，当前不能从此发布目录读取或授权问答。'
        } else {
            explanation.textContent = '此文件没有通过当前可访问的发布目录进入知识库。'
        }
        section.append(explanation)

        this.renderActivity(section, data.activity)

        if (data.source_state === 'in_scope' && data.knowledge_state === 'ready'
            && data.weknora_ask_url) {
            const link = document.createElement('a')
            link.className = 'weknora-sidebar__link'
            link.href = data.weknora_ask_url
            link.target = '_blank'
            link.rel = 'noopener noreferrer'
            link.textContent = '在知识库中提问此文件'
            section.append(link)
        } else if (data.source_state === 'in_scope' && data.weknora_login_url) {
            const link = document.createElement('a')
            link.className = 'weknora-sidebar__link'
            link.href = data.weknora_login_url
            link.target = '_blank'
            link.rel = 'noopener noreferrer'
            link.textContent = '以个人身份登录 WeKnora'
            section.append(link)
        }
        this.button(section, '刷新状态', () => this.load())
        this.replaceChildren(section)
    }

    addRow(section, label, value) {
        const row = document.createElement('p')
        row.className = 'weknora-sidebar__row'
        const strong = document.createElement('strong')
        strong.textContent = `${label}：`
        row.append(strong, document.createTextNode(value))
        section.append(row)
    }

    formatTime(unixSeconds) {
        if (!Number.isSafeInteger(unixSeconds) || unixSeconds <= 0) {
            return '未知'
        }
        return new Date(unixSeconds * 1000).toLocaleString()
    }
}

getSidebar().registerTab({
    id: 'integration_weknora',
    displayName: 'WeKnora',
    iconSvgInline: ICON,
    order: 75,
    tagName: TAG_NAME,
    enabled({ node }) {
        return ['file', 'folder', 'directory'].includes(node?.type)
    },
    onInit() {
        if (!customElements.get(TAG_NAME)) {
            customElements.define(TAG_NAME, WeknoraFileSidebarTab)
        }
    },
})
