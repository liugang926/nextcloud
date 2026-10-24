(function () {
    'use strict';

    function init() {
        const form = document.getElementById('weknora-setup-form');
        if (!form) return;

        const origin = document.getElementById('weknora-setup-origin');
        const token = document.getElementById('weknora-setup-token');
        const discover = document.getElementById('weknora-setup-discover');
        const save = document.getElementById('weknora-setup-save');
        const disconnect = document.getElementById('weknora-setup-disconnect');
        const message = document.getElementById('weknora-setup-message');
        const spacesPanel = document.getElementById('weknora-setup-spaces');
        const spaceList = document.getElementById('weknora-setup-space-list');
        const spaceCount = document.getElementById('weknora-setup-space-count');
        const current = document.getElementById('weknora-setup-current');
        const baseUrl = window.OC.generateUrl('/apps/integration_weknora/api/v1/admin/connection');
        const summary = window.WeKnoraAdminSummary || {};
        const { namedPrincipals, principalSummary } = summary;
        let discovered = [];
        let connectedSpaces = [];
        let editingVisibility = false;
        let connecting = false;
        let disconnectArmed = false;

        function setMessage(value, kind) {
            message.textContent = value;
            if (kind) message.dataset.kind = kind;
            else delete message.dataset.kind;
        }

        if (typeof namedPrincipals !== 'function' || typeof principalSummary !== 'function') {
            setMessage('WeKnora 管理界面脚本未完整加载，请刷新页面。', 'error');
            return;
        }

        function node(tag, className, value) {
            const element = document.createElement(tag);
            if (className) element.className = className;
            if (value !== undefined) element.textContent = value;
            return element;
        }

        async function request(method, url, payload) {
            const headers = {
                Accept: 'application/json',
                requesttoken: window.OC.requestToken || '',
                'X-Requested-With': 'XMLHttpRequest',
            };
            if (payload !== undefined) headers['Content-Type'] = 'application/json';
            const response = await fetch(url, {
                method,
                headers,
                credentials: 'same-origin',
                cache: 'no-store',
                body: payload === undefined ? undefined : JSON.stringify(payload),
            });
            let data = null;
            if ((response.headers.get('content-type') || '').includes('application/json')) {
                data = await response.json();
            }
            if (!response.ok) {
                const error = new Error('connection request failed');
                error.status = response.status;
                error.code = data && data.error;
                throw error;
            }
            return data || {};
        }

        function errorText(error) {
            if (error.status === 401 || error.status === 403) {
                return '管理员访问被拒绝。请检查当前会话后重新加载。';
            }
            return {
                connection_denied: 'WeKnora 拒绝了连接或授权。请确认连接密钥和空间权限。',
                connection_not_configured: '当前没有可更新的 WeKnora 连接。',
                invalid_visibility: '空间可见范围无效。请至少保留一个用户或组。',
                connection_unavailable: 'WeKnora 暂时不可用，连接状态未被修改。',
                invalid_connection: 'WeKnora 地址或连接密钥无效，或该地址未获运维批准。',
            }[error.code] || '操作未能完成。连接与已有发布来源均未被隐式修改。';
        }

        function spaceName(space) {
            return space.tenant_name || space.name || '未命名空间';
        }

        function spaceTemplate(space) {
            return space.processing_template_name || space.processing_template_id || '由 WeKnora 自动选择';
        }

        function header(editing) {
            const row = node('div',
                `weknora-admin__space-list-header${editing ? ' weknora-admin__space-list-header--editing' : ''}`);
            row.setAttribute('role', 'row');
            for (const label of editing
                ? ['允许', '空间', '默认处理方案', '可见范围 / 用户和组']
                : ['空间', '默认处理方案', '可见范围 / 用户和组']) {
                const cell = node('span', '', label);
                cell.setAttribute('role', 'columnheader');
                row.append(cell);
            }
            return row;
        }

        function identity(space, enabled) {
            const cell = node('div', 'weknora-admin__space-identity');
            cell.setAttribute('role', 'rowheader');
            const name = spaceName(space);
            const title = node('strong', 'weknora-admin__space-name', name);
            title.title = name;
            cell.append(title);
            if (spaceTemplate(space)) {
                const template = node('span', 'weknora-admin__space-tenant', spaceTemplate(space));
                template.title = template.textContent;
                cell.append(template);
            }
            const badge = node('span', 'weknora-admin__space-badge', enabled ? '已允许' : '未允许');
            badge.dataset.state = enabled ? 'enabled' : 'disabled';
            cell.append(badge);
            if (space.available === false) {
                cell.append(node('span', 'weknora-admin__space-reason',
                    space.reason || '当前连接密钥无权管理此空间'));
            }
            return cell;
        }

        function summaryRow(space) {
            const row = node('div', 'weknora-admin__space-row weknora-admin__space-row--summary');
            row.setAttribute('role', 'row');
            row.append(identity(space, true));
            const template = node('span', 'weknora-admin__space-cell-value', spaceTemplate(space));
            template.title = template.textContent;
            template.setAttribute('role', 'cell');
            row.append(template);
            const visibility = principalSummary(space.visibility);
            const access = node('div', 'weknora-admin__visibility-summary');
            access.setAttribute('role', 'cell');
            access.tabIndex = 0;
            access.title = visibility.full;
            access.setAttribute('aria-label', `可见范围：${visibility.full}`);
            access.append(node('span', 'weknora-admin__visibility-summary-text', visibility.text));
            row.append(access);
            return row;
        }

        function renderCurrent(data) {
            current.hidden = false;
            current.replaceChildren();
            disconnect.hidden = !data.connected;
            resetDisconnect();
            if (!data.connected) {
                connectedSpaces = [];
                current.append(node('p', '', '尚未连接 WeKnora。'));
                return;
            }
            connectedSpaces = Array.isArray(data.spaces) ? data.spaces : [];
            origin.value = data.origin || '';
            const title = node('p', 'weknora-admin__connected-title', `已连接：${data.origin}`);
            const count = node('p', '', `已授权空间：${connectedSpaces.length}`);
            current.append(title, count);

            if (data.web_url) {
                const row = node('p');
                row.append(document.createTextNode('WeKnora Web：'));
                const link = node('a', '', data.web_url);
                link.href = data.web_url;
                link.target = '_blank';
                link.rel = 'noopener noreferrer';
                row.append(link);
                current.append(row);
            } else {
                current.append(node('p', '', 'WeKnora Web 地址尚未由连接契约验证，目录用户暂时无法使用知识库入口。'));
            }
            current.append(node('p', '',
                `应用 ${data.app_version || '未知'} · 前端 ${String(data.bundle_version || '').slice(0, 12) || '未知'} · 契约 v${data.contract_version ?? '未知'}`));
            const health = data.compatible === true && data.health?.binding_allowed === true
                ? '正常：新目录可以绑定知识库。'
                : `降级：${data.health?.reason_code || '后台连接尚未验证'}。已有来源保持原有状态。`;
            const healthNode = node('p', '', `连接健康：${health}`);
            healthNode.dataset.kind = data.health?.binding_allowed === true ? 'success' : 'warning';
            current.append(healthNode);
            current.append(node('p', '', data.credentials_encrypted
                ? '空间级凭据：已加密保存。'
                : '空间级凭据：加密迁移待完成，暂不显示敏感信息。'));
            if (Number(data.stale_credential_count) > 0) {
                const cleanup = node('div', 'weknora-admin__notice');
                cleanup.append(node('p', '',
                    `有 ${data.stale_credential_count} 个旧空间凭据尚未确认远端撤销。新连接已生效，可安全重试清理。`));
                const retry = node('button', 'button', '重试清理旧凭据');
                retry.type = 'button';
                retry.addEventListener('click', async () => {
                    retry.disabled = true;
                    try {
                        const updated = await request('POST', `${baseUrl}/credentials/cleanup`, {});
                        renderCurrent(updated);
                        setMessage('旧凭据清理结果已更新。', 'success');
                    } catch (error) {
                        setMessage(errorText(error), 'error');
                        retry.disabled = false;
                    }
                });
                cleanup.append(retry);
                current.append(cleanup);
            }
            if (data.origin_approved === false) {
                current.append(node('p', 'weknora-admin__message', '当前地址不在运维批准列表中。已有连接只读保留，重新授权会被拒绝。'));
            }

            const list = node('div', 'weknora-admin__space-list');
            list.setAttribute('role', 'table');
            list.setAttribute('aria-label', 'WeKnora 已授权空间');
            list.append(header(false));
            for (const space of connectedSpaces) list.append(summaryRow(space));
            current.append(list);

            const impact = data.reconnect_impact || {};
            if (Number(impact.directory_count) > 0) {
                const note = node('div', 'weknora-admin__notice');
                note.append(node('h3', '', '重新授权影响预览'),
                    node('p', '', `当前 ${impact.directory_count} 个发布目录仍会继续运行。成功切换新授权后，旧空间级凭据会被撤销；断开管理连接不会停止这些来源。`));
                if (Array.isArray(impact.spaces) && impact.spaces.length) {
                    note.append(node('p', '',
                        `受影响空间：${impact.spaces.map((space) => space.space_name).join('、')}`));
                }
                if (Array.isArray(impact.directories) && impact.directories.length) {
                    const names = node('p', '', `受影响目录：${impact.directories.join('、')}`);
                    note.append(names);
                }
                current.append(note);
            }

            const edit = node('button', 'button', '管理空间可见范围');
            edit.type = 'button';
            edit.addEventListener('click', () => {
                renderSpaces(connectedSpaces.map((space) => ({ ...space, eligible: true })), true);
                setMessage('隐藏空间只会影响新目录候选；既有目录不会自动停止。');
                spacesPanel.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
            });
            current.append(edit);
        }

        function initialText(space, type) {
            const principals = Array.isArray(space.visibility?.[`${type}s`])
                ? space.visibility[`${type}s`] : [];
            const names = namedPrincipals(space.visibility, type === 'group' ? 'group' : 'user');
            return names.length ? names.map((entry) => entry.id).join(', ') : principals.join(', ');
        }

        function visibilityControls(space) {
            const cell = node('div', 'weknora-admin__space-access');
            cell.setAttribute('role', 'cell');
            const mode = node('select', 'weknora-admin__visibility-mode');
            mode.setAttribute('aria-label', '空间可见范围');
            mode.append(new Option('所有 Nextcloud 用户', 'all'),
                new Option('指定用户和组', 'restricted'),
                new Option('仅 Nextcloud 管理员', 'admin'));
            mode.value = ['all', 'restricted', 'admin'].includes(space.visibility?.mode)
                ? space.visibility.mode : 'all';
            const restricted = node('div');
            const groups = node('input');
            groups.type = 'text';
            groups.value = initialText(space, 'group');
            groups.placeholder = '组 ID，逗号分隔';
            groups.setAttribute('aria-label', '允许访问空间的 Nextcloud 组');
            const users = node('input');
            users.type = 'text';
            users.value = initialText(space, 'user');
            users.placeholder = '用户 UID，逗号分隔';
            users.setAttribute('aria-label', '允许访问空间的 Nextcloud 用户');
            restricted.append(node('label', '', '组'), groups, node('label', '', '用户'), users);
            restricted.hidden = mode.value !== 'restricted';
            mode.addEventListener('change', () => { restricted.hidden = mode.value !== 'restricted'; });
            cell.append(mode, restricted);
            return {
                cell,
                values() {
                    const list = (value) => value.split(',').map((entry) => entry.trim()).filter(Boolean);
                    return {
                        mode: mode.value,
                        groups: mode.value === 'restricted' ? list(groups.value) : [],
                        users: mode.value === 'restricted' ? list(users.value) : [],
                    };
                },
                disable(value) {
                    mode.disabled = value;
                    groups.disabled = value;
                    users.disabled = value;
                },
            };
        }

        function renderSpaces(spaces, visibilityOnly = false) {
            discovered = spaces;
            editingVisibility = visibilityOnly;
            spaceList.replaceChildren();
            spacesPanel.hidden = false;
            current.hidden = spaces.length > 0;
            save.hidden = spaces.length === 0;
            save.textContent = visibilityOnly ? '保存可见范围' : '保存连接';
            spaceList.setAttribute('role', 'table');
            spaceList.setAttribute('aria-label', 'WeKnora 空间授权');
            spaceList.append(header(true));
            const controls = [];
            let enabledCount = 0;

            for (const space of spaces) {
                const available = visibilityOnly || space.eligible === true;
                const row = node('div', 'weknora-admin__space-row weknora-admin__space-row--editing');
                row.setAttribute('role', 'row');
                row.classList.toggle('is-unavailable', !available);
                row.classList.toggle('is-selected', available);

                const allowCell = node('div', 'weknora-admin__space-allow-cell');
                allowCell.setAttribute('role', 'cell');
                let checkbox = null;
                if (!visibilityOnly) {
                    checkbox = node('input');
                    checkbox.type = 'checkbox';
                    checkbox.checked = available;
                    checkbox.disabled = !available;
                    checkbox.setAttribute('aria-label', `允许 ${spaceName(space)} 供 Nextcloud 目录使用`);
                    const label = node('label', 'weknora-admin__space-allow');
                    label.append(checkbox, node('span', '', '允许'));
                    allowCell.append(label);
                } else {
                    allowCell.append(node('span', 'weknora-admin__space-connected', '已连接'));
                }
                row.append(allowCell);
                const identityCell = identity(space, available);
                identityCell.classList.add('weknora-admin__space-identity--editing');
                row.append(identityCell);
                row.append(node('div', 'weknora-admin__space-template-cell', spaceTemplate(space)));
                const visibility = visibilityControls(space);
                row.append(visibility.cell);
                controls.push({ space, checkbox, visibility });
                checkbox?.addEventListener('change', () => {
                    row.classList.toggle('is-selected', checkbox.checked);
                    visibility.disable(!checkbox.checked);
                });
                visibility.disable(visibilityOnly ? false : !available);
                spaceList.append(row);
                if (available) enabledCount++;
            }
            const updateCount = () => {
                enabledCount = controls.filter((entry) =>
                    editingVisibility || entry.checkbox?.checked).length;
                spaceCount.textContent = editingVisibility
                    ? `${spaces.length} 个空间`
                    : `${enabledCount} / ${spaces.length} 个空间已允许`;
            };
            controls.forEach((entry) => entry.checkbox?.addEventListener('change', updateCount));
            updateCount();
            if (!spaces.length) {
                spaceList.append(node('p', 'weknora-admin__space-list-empty',
                    '没有发现可授权的空间。请确认连接密钥属于空间 Owner 或 Admin。'));
            }
        }

        async function loadStatus() {
            try {
                const data = await request('GET', baseUrl);
                renderCurrent(data);
            } catch (error) {
                setMessage(errorText(error), 'error');
            }
        }

        form.addEventListener('submit', async (event) => {
            event.preventDefault();
            if (connecting || !form.reportValidity()) return;
            connecting = true;
            discover.disabled = true;
            setMessage('正在联系 WeKnora 并发现空间…');
            try {
                const data = await request('POST', `${baseUrl}/discover`, {
                    origin: origin.value.trim(),
                    token: token.value,
                });
                renderSpaces(Array.isArray(data.spaces) ? data.spaces : [], false);
                setMessage('已发现空间。请明确选择允许使用的空间后保存连接。', 'success');
            } catch (error) {
                token.value = '';
                setMessage(errorText(error), 'error');
            } finally {
                connecting = false;
                discover.disabled = false;
            }
        });

        save.addEventListener('click', async () => {
            const rows = [...spaceList.querySelectorAll('.weknora-admin__space-row')];
            const controls = [];
            for (let index = 0; index < rows.length; index++) {
                const row = rows[index];
                const space = discovered[index];
                const checkbox = row.querySelector('.weknora-admin__space-allow-cell input[type="checkbox"]');
                if (!editingVisibility && !checkbox?.checked) continue;
                const mode = row.querySelector('.weknora-admin__visibility-mode');
                const inputs = row.querySelectorAll('.weknora-admin__space-access input');
                const list = (value) => value.split(',').map((entry) => entry.trim()).filter(Boolean);
                controls.push({
                    tenant_id: space.tenant_id,
                    visibility: {
                        mode: mode.value,
                        groups: mode.value === 'restricted' ? list(inputs[0].value) : [],
                        users: mode.value === 'restricted' ? list(inputs[1].value) : [],
                    },
                });
            }
            if (!controls.length) {
                setMessage('至少允许一个空间。', 'error');
                return;
            }
            save.disabled = true;
            try {
                const payload = {
                    origin: origin.value.trim(),
                    token: token.value,
                    spaces: controls,
                };
                const data = editingVisibility
                    ? await request('POST', `${baseUrl}/visibility`, { spaces: controls })
                    : await request('POST', baseUrl, payload);
                token.value = '';
                spacesPanel.hidden = true;
                renderCurrent(data);
                setMessage(editingVisibility
                    ? '空间可见范围已更新。既有来源未自动停止。'
                    : '连接已保存，空间级凭据已加密存储。', 'success');
            } catch (error) {
                setMessage(errorText(error), 'error');
            } finally {
                save.disabled = false;
            }
        });

        function resetDisconnect() {
            disconnectArmed = false;
            disconnect.textContent = '断开管理连接（不停止已有来源）';
        }

        disconnect.addEventListener('click', async () => {
            if (!disconnectArmed) {
                disconnectArmed = true;
                disconnect.textContent = '确认断开管理连接';
                setMessage('断开只清除本 Nextcloud 的管理连接和空间凭据，不会停止已有目录同步，也不会删除 WeKnora 知识库。再次点击确认。', 'warning');
                return;
            }
            disconnect.disabled = true;
            try {
                const data = await request('DELETE', baseUrl);
                renderCurrent(data);
                setMessage('管理连接已断开。既有来源是否继续由各来源的发布状态决定。', 'success');
            } catch (error) {
                setMessage(errorText(error), 'error');
                resetDisconnect();
            } finally {
                disconnect.disabled = false;
            }
        });

        loadStatus();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init, { once: true });
    } else {
        init();
    }
}());
