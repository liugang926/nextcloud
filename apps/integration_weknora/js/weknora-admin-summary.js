(function (root) {
    'use strict';

    function namedPrincipals(visibility, type) {
        const key = type === 'group' ? 'groups' : 'users';
        const raw = Array.isArray(visibility && visibility[key]) ? visibility[key] : [];
        const names = visibility && visibility.principal_names;
        const named = new Map();
        for (const entry of Array.isArray(names && names[key]) ? names[key] : []) {
            if (entry && typeof entry.id === 'string') {
                named.set(entry.id, String(entry.name || entry.id));
            }
        }
        return raw.map((id) => ({
            type,
            id: String(id),
            name: named.get(String(id)) || String(id),
        }));
    }

    function principalSummary(visibility, limit = 3) {
        const mode = visibility && visibility.mode;
        if (mode === 'admin') {
            const text = '仅 Nextcloud 管理员';
            return { text, full: text, count: 0 };
        }
        if (mode !== 'restricted') {
            const text = '所有 Nextcloud 用户';
            return { text, full: text, count: 0 };
        }
        const entries = [
            ...namedPrincipals(visibility, 'group'),
            ...namedPrincipals(visibility, 'user'),
        ].map((principal) => ({
            ...principal,
            label: `${principal.type === 'group' ? '组' : '用户'} ${principal.name}`,
        }));
        const shown = entries.slice(0, limit);
        const text = shown.length
            ? `${shown.map((principal) => principal.label).join('、')}` +
                (entries.length > limit ? ` +${entries.length - limit}` : '')
            : '未选择用户或组';
        return {
            text,
            full: entries.length ? entries.map((principal) => principal.label).join('\n') : text,
            count: entries.length,
        };
    }

    const api = Object.freeze({ namedPrincipals, principalSummary });
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    }
    if (root) {
        root.WeKnoraAdminSummary = api;
    }
}(typeof window === 'undefined' ? null : window));
