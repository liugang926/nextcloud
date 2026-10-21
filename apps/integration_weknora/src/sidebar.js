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
        if (!response.ok) throw new Error(data.error || 'directory_unavailable')
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
        this.addRow(section, '关联状态', targetDeleted ? '目标知识库或工作空间已删除' : data.inherited ? '继承发布根的知识库'
            : data.knowledge_base_id && connected ? '已关联知识库'
                : data.operation || data.knowledge_base_id ? '待连接' : '未关联')
        if (data.binding_name) this.addRow(section, '发布目录', data.binding_name)
        if (data.space_name) this.addRow(section, '空间', data.space_name)
        if (data.knowledge_base_id) this.addRow(section, '知识库', data.knowledge_base_id)
        if (data.publication_state) this.addRow(section, '发布', data.publication_state === 'active'
            ? connected ? '发布有效' : '待连接，发布待验证' : '已停止发布')
        if (typeof data.sync_enabled === 'boolean') this.addRow(section, '同步', data.sync_enabled ? '自动同步' : '已暂停同步')
        if (data.summary) {
            this.addRow(section, '当前可见文件', String(data.summary.total_visible ?? 0))
            this.addRow(section, '当前版本已完成', String(data.summary.current_completed ?? 0))
            this.addRow(section, '图谱结果', Object.entries(data.summary.graph_counts || {})
                .filter(([, count]) => count > 0)
                .map(([state, count]) => `${this.graphLabel(state)} ${count}`).join('；') || '暂无文件')
            this.addRow(section, '最后验证时间', this.formatTime(data.summary.last_verified_at))
            if (data.summary.status_stale) this.addRow(section, '状态验证', '需要刷新验证')
            if (data.summary.partial) this.addRow(section, '统计范围', '当前页可见文件，更多文件待加载')
            if (data.summary.next_cursor) this.button(section, '加载更多汇总', () => this.loadMoreSummary(data))
        }
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
                this.addRow(section, '连接原因', {
                    unsupported_source_acl: '当前挂载或分享权限模式不受支持',
                    permission_or_target_conflict: '管理委派、目录范围或目标知识库冲突',
                    target_reservation_unverified: 'WeKnora 目标空间或知识库预留尚未验证',
                    pairing_prepare_unverified: 'Nextcloud 尚未准备好知识库配对凭据',
                    pairing_remote_unverified: 'WeKnora 知识库来源配对尚未验证',
                    pairing_remote_transport: '无法连接 WeKnora 配对服务',
                    pairing_remote_invalid_response: 'WeKnora 配对服务返回了无法识别的响应',
                    pairing_remote_server_error: 'WeKnora 配对服务内部错误',
                    pairing_remote_server_error_source_prepare: 'WeKnora 创建知识库来源失败',
                    pairing_remote_server_error_onboarding: 'WeKnora 配对状态或管理员用途保存失败',
                    pairing_remote_rate_limited: 'WeKnora 配对服务请求过多',
                    pairing_remote_http_error: 'WeKnora 配对服务拒绝了请求',
                    pairing_remote_credential_unavailable: 'Nextcloud 中保存的 WeKnora 连接凭据不可用',
                    pairing_ack_unverified: '知识库配对确认尚未保存到 Nextcloud',
                    event_connection_pending: '知识库已创建，但 WeKnora 事件连接尚未建立',
                    grant_snapshot_unavailable: 'Nextcloud 目录权限快照无法生成',
                    grant_sync_unverified: 'WeKnora 目录权限同步尚未验证',
                    reconcile_pending: '完整目录清单尚未加入同步队列',
                    connection_unverified: '连接结果尚未验证，原文件已保留',
                }[data.operation.error_code] || '等待关联完成')
                this.button(section, '重试同一关联', async () => {
                    await this.api(`/directory-operations/${encodeURIComponent(data.operation.operation_id)}/retry`, 'POST', {})
                    this.load()
                })
            } else {
                this.button(section, '更换知识库', () => this.renderAssociation(false, true))
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
            for (const field of ['knowledge_counts', 'graph_counts']) {
                merged[field] = { ...previous[field] }
                for (const [state, count] of Object.entries(page[field])) merged[field][state] = (merged[field][state] || 0) + count
            }
            this._folderSummary = merged
            this.renderFolder({ ...data, ...next, summary: merged })
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
            try { await action() } catch (_) { this.renderMessage('操作未完成。请检查管理资格与配置后刷新重试。') }
            finally { button.disabled = false }
        })
        parent.append(button)
        return button
    }

    async renderAssociation(creating, migrating = false) {
        const selectedId = this.nodeId()
        const request = ++this._request
        const result = await this.api(`/folders/${selectedId}/candidates${creating ? '?for_creation=1' : ''}`)
        if (request !== this._request || selectedId !== this.nodeId() || !this.isConnected) return
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
        const spaces = result.targets || [{ tenant_id: result.tenant_id,
            processing_template_id: result.processing_template_id, candidates: result.candidates || [] }]
        if (!spaces.length) {
            this.renderMessage('目前没有本人获准创建或绑定的工作空间。请联系目录发布管理者。')
            return
        }
        const space = document.createElement('select')
        const seenSpaces = new Set()
        for (const candidate of spaces) {
            if (seenSpaces.has(String(candidate.tenant_id))) continue
            seenSpaces.add(String(candidate.tenant_id))
            const option = document.createElement('option')
            option.value = String(candidate.tenant_id)
            option.textContent = candidate.space_name || `空间 ${candidate.tenant_id}`
            space.append(option)
        }
        this.field(form, '工作空间', space)
        const name = document.createElement('input')
        name.type = 'text'; name.maxLength = 255; name.required = creating
        name.value = creating ? '' : this._node?.basename || this._node?.displayname || '目录知识库'
        this.field(form, creating ? '目录名称（同时作为知识库名称）' : '知识库名称', name)
        if (!creating) name.hidden = true
        const target = document.createElement('select')
        const automatic = document.createElement('option')
        automatic.value = ''; automatic.textContent = '新建知识库'
        let selectedTemplate = ''
        const updateCandidates = () => {
            target.replaceChildren(automatic)
            const selected = spaces.find(candidate => String(candidate.tenant_id) === space.value)
            selectedTemplate = selected ? String(selected.processing_template_id) : ''
            for (const candidate of selected?.candidates || []) {
                const option = document.createElement('option')
                option.value = candidate.id
                const contentCount = Number(candidate.content_count || 0)
                option.textContent = `已有知识库：${candidate.name}${contentCount > 0 ? `（已有 ${contentCount} 条内容）` : ''}`
                target.append(option)
            }
        }
        space.addEventListener('change', updateCandidates)
        updateCandidates()
        this.field(form, '目标知识库', target)
        const existingNote = document.createElement('p')
        existingNote.textContent = '选择已有知识库时，原有内容会保留；绑定后该知识库的新增内容由 Nextcloud 目录管理。'
        existingNote.className = 'weknora-sidebar__hint'
        form.append(existingNote)
        const submit = document.createElement('button')
        submit.type = 'submit'; submit.textContent = creating ? '创建并关联' : migrating ? '开始迁移' : '关联'
        form.append(submit)
        this.button(form, '取消', () => this.load())
        // Retain this operation and choice after transport failure. A retry
        // cannot create a second KB or silently replace the selected target.
        const operationId = crypto.randomUUID()
        let submittedChoice
        form.addEventListener('submit', async (event) => {
            event.preventDefault()
            submit.disabled = true
            name.disabled = true; target.disabled = true; space.disabled = true
            const selected = spaces.find(candidate => String(candidate.tenant_id) === space.value)
            const chosen = selected?.candidates?.find(candidate => String(candidate.id) === target.value)
            submittedChoice ||= { operation_id: operationId, mode: target.value ? 'existing' : 'create',
                name: chosen?.name || name.value, knowledge_base_id: target.value,
                folder_name: creating ? name.value : undefined,
                tenant_id: space.value, processing_template_id: selectedTemplate }
            if (migrating && !this._targetMigrations.has(selectedId)) {
                this._targetMigrations.set(selectedId, { operationId, choice: submittedChoice, confirmed: false })
            }
            try {
                const pending = migrating ? this._targetMigrations.get(selectedId) : null
                const path = pending?.confirmed ? `/folders/${selectedId}/target-migrations/${operationId}`
                    : `/folders/${selectedId}/${creating ? 'create' : migrating ? 'target-migration' : 'associate'}`
                const reply = await this.api(path, 'POST', pending?.confirmed ? {} : submittedChoice)
                if (selectedId !== this.nodeId()) return
                if (migrating) {
                    const current = this._targetMigrations.get(selectedId)
                    if (current) current.confirmed = true
                    if (reply.migration?.state === 'active') this._targetMigrations.delete(selectedId)
                    this.load()
                } else if (creating) {
                    this.renderMessage(reply.operation.state === 'active'
                        ? '目录已创建并关联。打开新目录的详情查看状态。'
                        : '目录已保留，关联正在重试。打开新目录的详情查看连接状态。')
                    this.button(this.firstElementChild, '刷新文件列表', () => window.location.reload())
                } else this.load()
            } catch (_) {
                const message = document.createElement('p')
                message.textContent = '未能确认关联结果。重试将继续同一操作。'
                form.append(message)
                submit.textContent = '重试同一操作'; submit.disabled = false
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
    }

    graphLabel(state) {
        return { not_enabled: '未启用', not_provided: '未提供', unsupported: '不支持', skipped: '已跳过', blocked: '已阻断',
            pending: '待处理', running: '处理中', succeeded: '已完成', succeeded_empty: '完成且无实体',
            partial_failed: '部分失败', failed: '失败', cancelled: '已取消', superseded: '已被新版本替代',
            unverified: '待验证' }[state] || '待验证'
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
        if (data.graph) {
            this.addRow(section, '文件图谱', this.graphLabel(data.graph.state))
            if (typeof data.graph.enabled === 'boolean') this.addRow(section, '图谱要求', !data.graph.enabled ? '未启用'
                : data.graph.required ? '图谱完成后发布' : '文档可独立发布')
            for (const [field, label] of Object.entries({ expected: '预期', succeeded: '成功', failed: '失败',
                skipped: '跳过', cancelled: '取消', pending: '待处理' })) {
                if (Number.isSafeInteger(data.graph[field])) this.addRow(section, `图谱任务${label}`, String(data.graph[field]))
            }
            if (data.graph.completed_at) this.addRow(section, '图谱完成时间', this.formatTime(data.graph.completed_at))
        }
        if (data.occurred_at) this.addRow(section, '状态更新时间', this.formatTime(data.occurred_at))
        if (data.last_verified_at) this.addRow(section, '最后验证时间', this.formatTime(data.last_verified_at))
        if (data.status_stale) this.addRow(section, '状态验证', '状态需要刷新验证')

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
