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
        const pairingBinding = document.getElementById('weknora-pairing-binding');
        const refreshPairing = document.getElementById('weknora-refresh-pairing');
        const pairingMessage = document.getElementById('weknora-pairing-message');
        const pairingDetails = document.getElementById('weknora-pairing-details');
        const connectionBinding = document.getElementById('weknora-connection-binding');
        const refreshConnection = document.getElementById('weknora-refresh-connection');
        const connectionMessage = document.getElementById('weknora-connection-message');
        const connectionDetails = document.getElementById('weknora-connection-details');
        const connectionForm = document.getElementById('weknora-connection-form');
        const connectionOrigin = document.getElementById('weknora-connection-origin');
        const connectionCredential = document.getElementById('weknora-connection-credential');
        const saveConnection = document.getElementById('weknora-save-connection');
        const revokeConnection = document.getElementById('weknora-revoke-connection');
        const cancelRevoke = document.getElementById('weknora-cancel-revoke');
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
        let connectionBusy = false;
        let connectionConfigured = false;
        let connectionRequestEpoch = 0;
        let pairingRequestEpoch = 0;
        let revokeArmed = false;

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

        function decimalGreater(left, right) {
            return left.length > right.length ||
                (left.length === right.length && left > right);
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
                case 'invalid_connection':
                case 'invalid_binding_id':
                    return 'The event credential or receiver address is invalid. Check the selected binding and approved WeKnora origin.';
                case 'connection_conflict':
                    return 'The event connection conflicts with an existing connection or pruned change history. Revoke the old local connection before pairing a new ID; pruned history needs operator reconciliation.';
                case 'binding_unavailable':
                    return 'The selected binding or its publication root is unavailable. Check the current folder, owner and overlap before resuming.';
                case 'pairing_unavailable':
                    return 'The local source pairing state is unavailable. Check the server logs and retry.';
                case 'connection_unavailable':
                    return 'The event connection service is unavailable. Check the server logs and retry.';
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
            const selectedPairing = preferredId || pairingBinding.value;
            const selectedConnection = preferredId || connectionBinding.value;
            connectionCredential.value = '';
            bindingRows.replaceChildren();
            publicationBinding.replaceChildren(new Option('Choose a binding', ''));
            pairingBinding.replaceChildren(new Option('Choose a binding', ''));
            connectionBinding.replaceChildren(new Option('Choose a binding', ''));

            if (bindings.length === 0) {
                const row = bindingRows.insertRow();
                const cell = row.insertCell();
                cell.colSpan = 6;
                cell.textContent = 'No bindings configured.';
            }

            bindings.forEach((binding) => {
                const row = bindingRows.insertRow();
                [binding.id, binding.name, binding.owner_uid, binding.root_file_id].forEach((value) => {
                    row.insertCell().textContent = String(value);
                });
                row.insertCell().textContent = binding.publication_state === 'stopped'
                    ? 'Stopped' : 'Active';
                const actions = row.insertCell();
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
                actions.append(edit);
                const toggle = document.createElement('button');
                toggle.type = 'button';
                toggle.className = 'button';
                toggle.textContent = binding.publication_state === 'stopped'
                    ? 'Resume publication' : 'Stop publication';
                toggle.addEventListener('click', async () => {
                    const stopping = binding.publication_state !== 'stopped';
                    if (stopping && toggle.dataset.armed !== 'true') {
                        toggle.dataset.armed = 'true';
                        toggle.textContent = 'Confirm stop';
                        message(bindingMessage, `Confirm stopping publication for ${binding.id}. New source reads and authorization will be denied; existing copies need WeKnora's retrieval guard.`, 'warning');
                        return;
                    }
                    toggle.disabled = true;
                    try {
                        const action = stopping ? 'stop' : 'resume';
                        const data = await request('POST',
                            `${bindingsUrl}/${encodeURIComponent(binding.id)}/${action}`, {});
                        const refreshed = await loadBindings(binding.id);
                        if (refreshed) {
                            message(bindingMessage,
                                `${binding.id}: publication ${data.publication_state}. ` +
                                (data.reconcile_hint_recorded
                                    ? 'A source reconciliation hint was recorded.'
                                    : 'The hint could not be recorded; verify the next full reconciliation.'),
                                data.reconcile_hint_recorded ? 'success' : 'warning');
                        }
                    } catch (error) {
                        toggle.disabled = false;
                        delete toggle.dataset.armed;
                        toggle.textContent = stopping ? 'Stop publication' : 'Resume publication';
                        message(bindingMessage, errorText(error), 'error');
                    }
                });
                actions.append(toggle);
                publicationBinding.add(new Option(`${binding.name} (${binding.id})`, binding.id));
                pairingBinding.add(new Option(`${binding.name} (${binding.id})`, binding.id));
                connectionBinding.add(new Option(`${binding.name} (${binding.id})`, binding.id));
            });

            if (bindings.some((binding) => binding.id === selected)) {
                publicationBinding.value = selected;
            }
            pairingBinding.value = bindings.some((binding) => binding.id === selectedPairing)
                ? selectedPairing : (bindings[0] ? bindings[0].id : '');
            connectionBinding.value = bindings.some((binding) => binding.id === selectedConnection)
                ? selectedConnection : (bindings[0] ? bindings[0].id : '');
            clearPublicationState();
            pairingDetails.hidden = true;
            refreshPairing.disabled = !pairingBinding.value;
            connectionConfigured = false;
            connectionDetails.hidden = true;
            resetRevoke();
            setConnectionControls();
        }

        async function loadBindings(preferredId) {
            refreshBindings.disabled = true;
            message(bindingMessage, 'Loading bindings…', '');
            try {
                const data = await request('GET', bindingsUrl);
                if (!Array.isArray(data.bindings)) {
                    throw new Error('The server returned an invalid binding list.');
                }
                if (data.bindings.some((binding) => !binding ||
                    !['active', 'stopped'].includes(binding.publication_state) ||
                    !Number.isSafeInteger(binding.publication_epoch) ||
                    binding.publication_epoch < 0)) {
                    throw new Error('The server returned an invalid binding publication state.');
                }
                bindings = data.bindings;
                renderBindings(preferredId);
                message(bindingMessage, `${bindings.length} binding(s) loaded.`, 'success');
                loadPairingStatus();
                loadConnectionStatus();
                return true;
            } catch (error) {
                message(bindingMessage, errorText(error), 'error');
                return false;
            } finally {
                refreshBindings.disabled = false;
            }
        }

        function connectionUrl(binding) {
            if (!bindings.some((entry) => entry.id === binding)) {
                throw new Error('Choose a configured binding.');
            }
            return `${bindingsUrl}/${encodeURIComponent(binding)}/event-connection`;
        }

        async function loadPairingStatus() {
            const binding = pairingBinding.value;
            const epoch = ++pairingRequestEpoch;
            pairingDetails.hidden = true;
            refreshPairing.disabled = !binding;
            if (!binding) {
                message(pairingMessage, 'Create or choose a binding to inspect source pairing.', '');
                return;
            }
            refreshPairing.disabled = true;
            message(pairingMessage, 'Loading local source pairing…', '');
            try {
                const data = await request('GET',
                    `${bindingsUrl}/${encodeURIComponent(binding)}/source-pairing`);
                if (epoch !== pairingRequestEpoch || binding !== pairingBinding.value) {
                    return;
                }
                const pairing = data.pairing;
                if (!pairing || pairing.binding_id !== binding ||
                    !['pending', 'active', 'aborted'].includes(pairing.state) ||
                    typeof pairing.operation_id !== 'string' ||
                    typeof pairing.instance_id !== 'string' ||
                    typeof pairing.tenant_id !== 'string' ||
                    typeof pairing.knowledge_base_id !== 'string' ||
                    !(pairing.data_source_id === null || typeof pairing.data_source_id === 'string') ||
                    typeof pairing.key_id !== 'string' ||
                    !Number.isSafeInteger(pairing.publication_epoch) ||
                    pairing.publication_epoch < 0 || 'token' in data) {
                    throw new Error('The server returned invalid source pairing state.');
                }
                const values = {
                    state: pairing.state,
                    operation: pairing.operation_id,
                    instance: pairing.instance_id,
                    tenant: pairing.tenant_id,
                    kb: pairing.knowledge_base_id,
                    source: pairing.data_source_id || 'None yet',
                    key: pairing.key_id,
                    epoch: String(pairing.publication_epoch),
                };
                Object.entries(values).forEach(([field, value]) => {
                    document.getElementById(`weknora-pairing-${field}`).textContent = value;
                });
                pairingDetails.hidden = false;
                message(pairingMessage,
                    pairing.state === 'active'
                        ? 'Nextcloud committed this source. Confirm the same active operation in WeKnora; this does not confirm indexing.'
                        : pairing.state === 'pending'
                            ? 'Nextcloud is waiting for the matching WeKnora commit. Retry or abort this operation through the operator workflow.'
                            : 'The last local pairing was aborted. Start a new operation through the operator workflow.',
                    pairing.state === 'active' ? 'success' : 'warning');
            } catch (error) {
                if (epoch !== pairingRequestEpoch || binding !== pairingBinding.value) {
                    return;
                }
                pairingDetails.hidden = true;
                if (error.status === 404 && error.code === 'pairing_not_found') {
                    message(pairingMessage, 'No source pairing has been prepared for this binding.', '');
                } else {
                    message(pairingMessage, errorText(error), 'error');
                }
            } finally {
                if (epoch === pairingRequestEpoch) {
                    refreshPairing.disabled = false;
                }
            }
        }

        function resetRevoke() {
            revokeArmed = false;
            revokeConnection.textContent = 'Revoke local connection';
            cancelRevoke.hidden = true;
        }

        function setConnectionControls() {
            const hasBinding = bindings.some((entry) => entry.id === connectionBinding.value);
            connectionBinding.disabled = connectionBusy || bindings.length === 0;
            refreshConnection.disabled = connectionBusy || !hasBinding;
            saveConnection.disabled = connectionBusy || !hasBinding;
            revokeConnection.hidden = !connectionConfigured;
            revokeConnection.disabled = connectionBusy || !hasBinding;
            cancelRevoke.disabled = connectionBusy;
        }

        function renderConnection(data, binding) {
            if (data.binding_id !== binding || typeof data.connection_id !== 'string' ||
                typeof data.key_id !== 'string' || typeof data.receiver_url !== 'string' ||
                typeof data.status !== 'string' ||
                typeof data.received_through_event_id !== 'string' ||
                !/^(0|[1-9][0-9]*)$/.test(data.received_through_event_id) ||
                typeof data.applied_through_event_id !== 'string' ||
                !/^(0|[1-9][0-9]*)$/.test(data.applied_through_event_id) ||
                decimalGreater(data.applied_through_event_id, data.received_through_event_id) ||
                !Number.isSafeInteger(data.applied_checked_at) || data.applied_checked_at < 0 ||
                typeof data.applied_error_code !== 'string' ||
                !Number.isSafeInteger(data.attempt_count) || data.attempt_count < 0 ||
                !Number.isSafeInteger(data.next_attempt_at) || data.next_attempt_at < 0 ||
                typeof data.last_error_code !== 'string') {
                throw new Error('The server returned invalid event connection status.');
            }
            document.getElementById('weknora-connection-status').textContent = data.status;
            document.getElementById('weknora-connection-id').textContent = data.connection_id;
            document.getElementById('weknora-connection-key-id').textContent = data.key_id;
            document.getElementById('weknora-connection-receiver').textContent = data.receiver_url;
            document.getElementById('weknora-connection-received').textContent =
                data.received_through_event_id === '0' ? 'None yet (0)' : data.received_through_event_id;
            document.getElementById('weknora-connection-applied').textContent =
                data.applied_through_event_id === '0' ? 'None verified (0)' : data.applied_through_event_id;
            document.getElementById('weknora-connection-applied-checked').textContent =
                data.applied_checked_at === 0 ? 'Never' :
                    new Date(data.applied_checked_at * 1000).toLocaleString();
            document.getElementById('weknora-connection-applied-error').textContent =
                data.applied_error_code || 'None';
            document.getElementById('weknora-connection-attempts').textContent = String(data.attempt_count);
            document.getElementById('weknora-connection-next-attempt').textContent =
                data.next_attempt_at === 0 ? 'None scheduled' : new Date(data.next_attempt_at * 1000).toLocaleString();
            document.getElementById('weknora-connection-error').textContent = data.last_error_code || 'None';
            connectionDetails.hidden = false;
            connectionConfigured = true;
            try {
                connectionOrigin.value = new URL(data.receiver_url).origin;
            } catch (_) {
                // Status is still safe to display if an older receiver URL is invalid.
            }
            setConnectionControls();
        }

        async function loadConnectionStatus() {
            const binding = connectionBinding.value;
            const epoch = ++connectionRequestEpoch;
            connectionCredential.value = '';
            resetRevoke();
            connectionConfigured = false;
            connectionDetails.hidden = true;
            setConnectionControls();
            if (!binding) {
                message(connectionMessage, 'Create or choose a binding to inspect event delivery.', '');
                return;
            }
            message(connectionMessage, 'Loading local event connection…', '');
            try {
                const data = await request('GET', connectionUrl(binding));
                if (epoch !== connectionRequestEpoch || binding !== connectionBinding.value) {
                    return;
                }
                renderConnection(data, binding);
                if (data.status !== 'active') {
                    message(connectionMessage,
                        `Local delivery is ${data.status}. Inspect the error code and connection state.`, 'warning');
                } else if (data.applied_checked_at === 0) {
                    message(connectionMessage,
                        'No signed applied status has been verified yet. A durable receipt is not proof of indexing.',
                        'warning');
                } else if (data.applied_error_code) {
                    message(connectionMessage,
                        'The latest applied-status check was not verified. Inspect its error code; received hints are not proof of indexing.',
                        'warning');
                } else if (decimalGreater(data.received_through_event_id,
                    data.applied_through_event_id)) {
                    message(connectionMessage,
                        'The durable receipt is ahead of the last verified applied watermark. Synchronization or indexing may still be in progress.',
                        'warning');
                } else {
                    message(connectionMessage,
                        'The last verified applied watermark covers this connection\'s recorded receipt. Check each file status before treating it as ready.',
                        'success');
                }
            } catch (error) {
                if (epoch !== connectionRequestEpoch || binding !== connectionBinding.value) {
                    return;
                }
                if (error.status === 404 && error.code === 'connection_not_found') {
                    message(connectionMessage, 'No local event connection is configured for this binding.', '');
                } else {
                    message(connectionMessage, errorText(error), 'error');
                }
            }
        }

        function parseConnectionCredential(text, binding, originText) {
            let credential;
            try {
                credential = JSON.parse(text);
            } catch (_) {
                throw new Error('Paste the complete one-time JSON response from WeKnora Pair or Rotate.');
            }
            if (!credential || typeof credential !== 'object' || Array.isArray(credential) ||
                ['binding_id', 'nextcloud_instance_id', 'connection_id', 'key_id', 'secret', 'receiver_url']
                    .some((field) => typeof credential[field] !== 'string' || credential[field] === '')) {
                throw new Error('The one-time response is missing required event connection fields.');
            }
            if (credential.binding_id !== binding) {
                throw new Error('The one-time response belongs to a different binding.');
            }
            let origin;
            try {
                origin = new URL(originText);
            } catch (_) {
                throw new Error('Enter the approved WeKnora origin, including http:// or https://.');
            }
            if (!['http:', 'https:'].includes(origin.protocol) || origin.username || origin.password ||
                (origin.pathname !== '/' && origin.pathname !== '') || origin.search || origin.hash) {
                throw new Error('Enter only the WeKnora origin, without a path, credentials, query, or fragment.');
            }
            const receiverUrl = `${origin.origin}/api/v1/integrations/nextcloud/events`;
            if (credential.receiver_url !== '/api/v1/integrations/nextcloud/events' &&
                credential.receiver_url !== receiverUrl) {
                throw new Error('The one-time response has a different event receiver address.');
            }
            return {
                binding_id: credential.binding_id,
                nextcloud_instance_id: credential.nextcloud_instance_id,
                connection_id: credential.connection_id,
                key_id: credential.key_id,
                secret: credential.secret,
                receiver_url: receiverUrl,
            };
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
                    typeof data.consumer_acknowledgement_available !== 'boolean') {
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
                document.getElementById('weknora-diagnostics-applied-ack').textContent =
                    data.consumer_acknowledgement_available ? 'Available' : 'Unavailable';
                diagnosticsValues.hidden = false;
                message(diagnosticsMessage, 'Source diagnostics refreshed.', 'success');
            } catch (error) {
                diagnosticsValues.hidden = true;
                message(diagnosticsMessage, errorText(error), 'error');
            } finally {
                refreshDiagnostics.disabled = false;
            }
        }

        connectionBinding.addEventListener('change', () => {
            connectionCredential.value = '';
            connectionOrigin.value = '';
            loadConnectionStatus();
        });
        pairingBinding.addEventListener('change', loadPairingStatus);
        refreshPairing.addEventListener('click', loadPairingStatus);
        refreshConnection.addEventListener('click', loadConnectionStatus);
        connectionForm.addEventListener('submit', async (event) => {
            event.preventDefault();
            if (connectionBusy) {
                return;
            }
            if (!connectionForm.reportValidity()) {
                connectionCredential.value = '';
                return;
            }
            const binding = connectionBinding.value;
            let rawCredential = connectionCredential.value;
            connectionCredential.value = '';
            let payload;
            try {
                connectionUrl(binding);
                payload = parseConnectionCredential(rawCredential, binding, connectionOrigin.value.trim());
            } catch (error) {
                message(connectionMessage, error.message, 'error');
                return;
            } finally {
                rawCredential = '';
            }
            connectionBusy = true;
            ++connectionRequestEpoch;
            resetRevoke();
            setConnectionControls();
            message(connectionMessage, 'Installing event connection credential…', '');
            try {
                const data = await request('POST', connectionUrl(binding), payload);
                renderConnection(data, binding);
                message(connectionMessage,
                    'Event credential installed. Delivery starts with the next local worker run; the receipt ID does not confirm WeKnora indexing.',
                    'success');
            } catch (error) {
                message(connectionMessage, errorText(error), 'error');
            } finally {
                payload.secret = '';
                connectionBusy = false;
                setConnectionControls();
            }
        });
        revokeConnection.addEventListener('click', async () => {
            if (connectionBusy || !connectionConfigured) {
                return;
            }
            const binding = connectionBinding.value;
            if (!revokeArmed) {
                revokeArmed = true;
                revokeConnection.textContent = 'Confirm local revocation';
                cancelRevoke.hidden = false;
                message(connectionMessage,
                    `Confirm local revocation for ${binding}. Delivery will stop; revoke the paired connection in WeKnora separately.`,
                    'warning');
                return;
            }
            connectionCredential.value = '';
            connectionBusy = true;
            ++connectionRequestEpoch;
            setConnectionControls();
            try {
                const data = await request('DELETE', connectionUrl(binding));
                if (data.revoked !== true) {
                    throw new Error('The server returned an invalid revocation response.');
                }
                connectionConfigured = false;
                connectionDetails.hidden = true;
                resetRevoke();
                message(connectionMessage,
                    'Local event delivery stopped and its credential was removed. Revoke the connection in WeKnora separately.',
                    'success');
            } catch (error) {
                resetRevoke();
                message(connectionMessage, errorText(error), 'error');
            } finally {
                connectionBusy = false;
                setConnectionControls();
            }
        });
        cancelRevoke.addEventListener('click', () => {
            connectionCredential.value = '';
            resetRevoke();
            message(connectionMessage, 'Local revocation cancelled.', '');
        });

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
        setConnectionControls();
        loadBindings();
        loadDiagnostics();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init, { once: true });
    } else {
        init();
    }
}());
