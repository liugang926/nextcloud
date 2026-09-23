import { getSidebar } from '@nextcloud/files'

const TAG_NAME = 'integration_weknora-files-sidebar-tab'
const ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M4 3h11l5 5v13H4V3zm2 2v14h12V9h-4V5H6zm2 7h8v2H8v-2zm0 3h6v2H8v-2z"/></svg>'

class WeknoraFileSidebarTab extends HTMLElement {
    constructor() {
        super()
        this._node = null
        this._active = false
        this._request = 0
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
        this.renderMessage('正在读取文件发布状态…')
        try {
            const url = window.OC.generateUrl(`/apps/integration_weknora/api/v1/files/${encodeURIComponent(fileId)}/status`)
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
            if (request !== this._request || !this.isConnected) {
                return
            }
            this.renderStatus(data)
        } catch (_) {
            if (request === this._request && this.isConnected) {
                this.renderMessage('暂时无法确认发布状态，请稍后重试。')
            }
        }
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
                failed: '当前版本解析失败',
                unverified: '尚未验证',
            }[data.knowledge_state] || '尚未验证'
            this.addRow(section, '知识状态', knowledgeState)
        }
        this.addRow(section, '当前源版本', data.source_etag || '未知')
        this.addRow(section, '已发布源版本', data.published_source_etag || '尚未验证')
        this.addRow(section, '知识就绪时间', this.formatTime(data.knowledge_ready_at))

        const explanation = document.createElement('p')
        explanation.className = 'weknora-sidebar__explanation'
        if (data.source_state === 'in_scope') {
            explanation.textContent = {
                ready: 'WeKnora 已解析并发布此源文件的当前版本。能否提问仍取决于你在 WeKnora 登录后的个人权限。',
                updating: 'WeKnora 尚未发布此源文件的当前版本。请稍后刷新。',
                failed: 'WeKnora 处理此源文件的当前版本时失败。请联系管理员查看同步与解析记录。',
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

        if (data.source_state === 'in_scope' && data.weknora_login_url) {
            const link = document.createElement('a')
            link.className = 'weknora-sidebar__link'
            link.href = data.weknora_login_url
            link.target = '_blank'
            link.rel = 'noopener noreferrer'
            link.textContent = '以个人身份登录 WeKnora 提问'
            section.append(link)
        }
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
        return node?.type === 'file'
    },
    onInit() {
        if (!customElements.get(TAG_NAME)) {
            customElements.define(TAG_NAME, WeknoraFileSidebarTab)
        }
    },
})
