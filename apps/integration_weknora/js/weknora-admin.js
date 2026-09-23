(function () {
    'use strict';

    function init() {
        const root = document.getElementById('weknora-admin');
        if (!root) {
            return;
        }

        const bindingsUrl = root.dataset.bindingsUrl;
        const diagnosticsUrl = root.dataset.diagnosticsUrl;
        const refreshDiagnostics = document.getElementById('weknora-refresh-diagnostics');
        const diagnosticsMessage = document.getElementById('weknora-diagnostics-message');
        const diagnosticsValues = document.getElementById('weknora-diagnostics-values');
        const bindingForm = document.getElementById('weknora-binding-form');
        const bindingId = document.getElementById('weknora-binding-id');
        const bindingName = document.getElementById('weknora-binding-name');
        const ownerUid = document.getElementById('weknora-owner-uid');
        const rootFileId = document.getElementById('weknora-root-file-id');
        const saveBinding = document.getElementById('weknora-save-binding');
        const refreshBindings = document.getElementById('weknora-refresh-bindings');
        const bindingMessage = document.getElementById('weknora-binding-message');
        const bindingRows = document.getElementById('weknora-bindings-list');
        const publicationForm = document.getElementById('weknora-publication-form');
        const publicationBinding = document.getElementById('weknora-publication-binding');
        const publicationFileId = document.getElementById('weknora-publication-file-id');
        const checkState = document.getElementById('weknora-check-state');
        const withdraw = document.getElementById('weknora-withdraw');
        const cancelWithdrawal = document.getElementById('weknora-cancel-withdrawal');
        const republish = document.getElementById('weknora-republish');
        const publicationMessage = document.getElementById('weknora-publication-message');

        let bindings = [];
        let currentState = null;
        let publicationBusy = false;
        let withdrawalArmed = false;

        function message(element, value, kind) {
            element.textContent = value;
            if (kind) {
                element.dataset.kind = kind;
            } else {
                delete element.dataset.kind;
            }
        }

        function positiveFileId(input) {
            const value = input.value.trim();
            const parsed = Number(value);
            if (!/^[1-9][0-9]*$/.test(value) || !Number.isSafeInteger(parsed)) {
                throw new Error('Enter a positive file ID within the browser\'s safe integer range.');
            }
            return parsed;
        }

        async function request(method, url, payload) {
            // These routes deliberately use the administrator's session, never a service token.
            const token = window.OC && window.OC.requestToken;
            if (!token) {
                throw new Error('The Nextcloud request token is missing. Reload this settings page.');
            }
            const headers = {
                'Accept': 'application/json',
                'requesttoken': token,
                'X-Requested-With': 'XMLHttpRequest',
            };
            if (payload !== undefined) {
                headers['Content-Type'] = 'application/json';
            }
            const response = await fetch(url, {
                method,
                headers,
                body: payload === undefined ? undefined : JSON.stringify(payload),
                credentials: 'same-origin',
                cache: 'no-store',
            });
            let data = null;
            if ((response.headers.get('content-type') || '').includes('application/json')) {
                data = await response.json();
            }
            if (!response.ok) {
                const error = new Error('Request failed');
                error.status = response.status;
                error.code = data && data.error;
                throw error;
            }
            if (!data || typeof data !== 'object') {
                throw new Error('The server returned an unexpected response.');
            }
            return data;
        }

        function errorText(error) {
            if (error.status === 401 || error.status === 403) {
                return 'Administrator access was denied. Check your session and reload the page.';
            }
            switch (error.code) {
                case 'invalid_binding':
                    return 'The binding details are invalid. Check the ID, owner UID and root folder file ID.';
                case 'binding_conflict':
                    return 'The binding conflicts with an existing root or owner. Existing binding roots cannot be changed.';
                case 'invalid_configuration':
                    return 'The stored binding configuration is invalid. Check the app configuration.';
                case 'binding_registry_unavailable':
                case 'publication_state_unavailable':
                case 'diagnostics_unavailable':
                    return 'The publication service is unavailable. Try again after checking the server logs.';
                case 'not_found':
                    return 'The binding or file ID was not found in the allowed scope.';
                default:
                    return error.message || 'The request failed. Please try again.';
            }
        }

        function setPublicationButtons() {
            let valid = bindings.some((binding) => binding.id === publicationBinding.value);
            try {
                positiveFileId(publicationFileId);
            } catch (_) {
                valid = false;
            }
            checkState.disabled = publicationBusy || !valid;
            withdraw.disabled = publicationBusy || !valid;
            republish.disabled = publicationBusy || !valid || currentState !== 'withdrawn';
        }

        function resetWithdrawal() {
            withdrawalArmed = false;
            withdraw.textContent = 'Withdraw and exclude';
            cancelWithdrawal.hidden = true;
        }

        function clearPublicationState() {
            currentState = null;
            resetWithdrawal();
            message(publicationMessage, '', '');
            setPublicationButtons();
        }

        function renderBindings(preferredId) {
            const selected = preferredId || publicationBinding.value;
            bindingRows.replaceChildren();
            publicationBinding.replaceChildren(new Option('Choose a binding', ''));

            if (bindings.length === 0) {
                const row = bindingRows.insertRow();
                const cell = row.insertCell();
                cell.colSpan = 5;
                cell.textContent = 'No bindings configured.';
            }

            bindings.forEach((binding) => {
                const row = bindingRows.insertRow();
                [binding.id, binding.name, binding.owner_uid, binding.root_file_id].forEach((value) => {
                    row.insertCell().textContent = String(value);
                });
                const edit = document.createElement('button');
                edit.type = 'button';
                edit.className = 'button';
                edit.textContent = 'Edit name';
                edit.addEventListener('click', () => {
                    bindingId.value = binding.id;
                    bindingName.value = binding.name;
                    ownerUid.value = binding.owner_uid;
                    rootFileId.value = String(binding.root_file_id);
                    bindingName.focus();
                });
                row.insertCell().append(edit);
                publicationBinding.add(new Option(`${binding.name} (${binding.id})`, binding.id));
            });

            if (bindings.some((binding) => binding.id === selected)) {
                publicationBinding.value = selected;
            }
            clearPublicationState();
        }

        async function loadBindings(preferredId) {
            refreshBindings.disabled = true;
            message(bindingMessage, 'Loading bindings…', '');
            try {
                const data = await request('GET', bindingsUrl);
                if (!Array.isArray(data.bindings)) {
                    throw new Error('The server returned an invalid binding list.');
                }
                bindings = data.bindings;
                renderBindings(preferredId);
                message(bindingMessage, `${bindings.length} binding(s) loaded.`, 'success');
                return true;
            } catch (error) {
                message(bindingMessage, errorText(error), 'error');
                return false;
            } finally {
                refreshBindings.disabled = false;
            }
        }

        function formatAge(seconds) {
            if (!Number.isSafeInteger(seconds) || seconds < 0) {
                return 'None retained';
            }
            if (seconds < 3600) {
                return `${Math.floor(seconds / 60)} minute(s)`;
            }
            if (seconds < 86400) {
                return `${Math.floor(seconds / 3600)} hour(s)`;
            }
            return `${Math.floor(seconds / 86400)} day(s)`;
        }

        async function loadDiagnostics() {
            refreshDiagnostics.disabled = true;
            message(diagnosticsMessage, 'Loading source diagnostics…', '');
            try {
                const data = await request('GET', diagnosticsUrl);
                if (!Number.isSafeInteger(data.binding_count) || data.binding_count < 0 ||
                    typeof data.binding_roots_available !== 'boolean' ||
                    !Number.isSafeInteger(data.retained_change_hints) || data.retained_change_hints < 0 ||
                    !Number.isSafeInteger(data.explicit_withdrawal_count) || data.explicit_withdrawal_count < 0 ||
                    data.consumer_acknowledgement_available !== false) {
                    throw new Error('The server returned invalid source diagnostics.');
                }
                document.getElementById('weknora-diagnostics-roots').textContent =
                    data.binding_roots_available ? `${data.binding_count} configured, available` :
                        `${data.binding_count} configured, one or more unavailable`;
                document.getElementById('weknora-diagnostics-hints').textContent =
                    String(data.retained_change_hints);
                document.getElementById('weknora-diagnostics-oldest').textContent =
                    formatAge(data.oldest_retained_hint_age_seconds);
                document.getElementById('weknora-diagnostics-newest').textContent =
                    Number.isSafeInteger(data.newest_change_hint_at) && data.newest_change_hint_at > 0
                        ? new Date(data.newest_change_hint_at * 1000).toLocaleString()
                        : 'None recorded';
                document.getElementById('weknora-diagnostics-withdrawals').textContent =
                    String(data.explicit_withdrawal_count);
                diagnosticsValues.hidden = false;
                message(diagnosticsMessage, 'Source diagnostics refreshed.', 'success');
            } catch (error) {
                diagnosticsValues.hidden = true;
                message(diagnosticsMessage, errorText(error), 'error');
            } finally {
                refreshDiagnostics.disabled = false;
            }
        }

        bindingForm.addEventListener('submit', async (event) => {
            event.preventDefault();
            if (!bindingForm.reportValidity()) {
                return;
            }
            const id = bindingId.value.trim();
            const name = bindingName.value.trim();
            const owner = ownerUid.value.trim();
            if (!/^[A-Za-z0-9_-]{1,128}$/.test(id) || !name || !owner) {
                message(bindingMessage, 'Enter a valid binding ID, name and owner UID.', 'error');
                return;
            }
            let rootId;
            try {
                rootId = positiveFileId(rootFileId);
            } catch (error) {
                message(bindingMessage, error.message, 'error');
                return;
            }
            saveBinding.disabled = true;
            try {
                await request('POST', bindingsUrl, {
                    id,
                    name,
                    owner_uid: owner,
                    root_file_id: rootId,
                });
                const refreshed = await loadBindings(id);
                if (refreshed) {
                    message(bindingMessage, `Binding ${id} saved.`, 'success');
                }
            } catch (error) {
                message(bindingMessage, errorText(error), 'error');
            } finally {
                saveBinding.disabled = false;
            }
        });

        refreshBindings.addEventListener('click', () => loadBindings());
        refreshDiagnostics.addEventListener('click', loadDiagnostics);
        publicationBinding.addEventListener('change', clearPublicationState);
        publicationFileId.addEventListener('input', clearPublicationState);

        function publicationUrl(action) {
            const binding = publicationBinding.value;
            const fileId = positiveFileId(publicationFileId);
            if (!bindings.some((entry) => entry.id === binding)) {
                throw new Error('Choose a configured binding.');
            }
            return `${bindingsUrl}/${encodeURIComponent(binding)}/files/${fileId}/${action}`;
        }

        async function publicationRequest(method, action) {
            if (publicationBusy) {
                return;
            }
            resetWithdrawal();
            publicationBusy = true;
            setPublicationButtons();
            message(publicationMessage, 'Checking publication decision…', '');
            try {
                const data = await request(method, publicationUrl(action), method === 'POST' ? {} : undefined);
                if (data.state !== 'eligible' && data.state !== 'withdrawn') {
                    throw new Error('The server returned an invalid publication state.');
                }
                currentState = data.state;
                if (data.state === 'withdrawn') {
                    message(publicationMessage, 'Withdrawn: this file ID is excluded from the app manifest and content API.', 'success');
                } else {
                    message(publicationMessage, 'Eligible: the file can appear in the app manifest. This does not confirm WeKnora indexing.', 'success');
                }
            } catch (error) {
                currentState = null;
                if (method === 'GET' && error.status === 404) {
                    message(publicationMessage, 'No current readable file or recorded decision for this binding and file ID. You can still reserve an exclusion with Withdraw.', 'error');
                } else {
                    message(publicationMessage, errorText(error), 'error');
                }
            } finally {
                publicationBusy = false;
                setPublicationButtons();
            }
        }

        publicationForm.addEventListener('submit', (event) => {
            event.preventDefault();
            if (publicationForm.reportValidity()) {
                publicationRequest('GET', 'publication');
            }
        });
        withdraw.addEventListener('click', () => {
            if (!publicationForm.reportValidity()) {
                return;
            }
            if (!withdrawalArmed) {
                withdrawalArmed = true;
                withdraw.textContent = 'Confirm withdrawal';
                cancelWithdrawal.hidden = false;
                message(publicationMessage, `Confirm withdrawal of file ID ${publicationFileId.value.trim()} from ${publicationBinding.value}. It will be excluded from this app's read API; existing WeKnora copies need separate cleanup.`, 'warning');
                return;
            }
            publicationRequest('POST', 'withdraw');
        });
        cancelWithdrawal.addEventListener('click', () => {
            resetWithdrawal();
            message(publicationMessage, 'Withdrawal cancelled.', '');
        });
        republish.addEventListener('click', () => {
            if (publicationForm.reportValidity()) {
                publicationRequest('POST', 'republish');
            }
        });

        setPublicationButtons();
        loadBindings();
        loadDiagnostics();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init, { once: true });
    } else {
        init();
    }
}());
