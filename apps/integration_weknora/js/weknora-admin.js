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
        const pairingPending = document.getElementById('weknora-pairing-pending');
        const pairingStatusPath = document.getElementById('weknora-pairing-status-path');
        const pairingRetryPath = document.getElementById('weknora-pairing-retry-path');
        const connectionBinding = document.getElementById('weknora-connection-binding');
        const refreshConnection = document.getElementById('weknora-refresh-connection');
        const connectionMessage = document.getElementById('weknora-connection-message');
        const connectionDetails = document.getElementById('weknora-connection-details');
        const connectionForm = document.getElementById('weknora-connection-form');
        const connectionOrigin = document.getElementById('weknora-connection-origin');
        const connectionCredential = document.getElementById('weknora-connection-credential');
        const saveConnection = document.getElementById('weknora-save-connection');
        const retryConnection = document.getElementById('weknora-retry-connection');
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
        let pausedConnection = null;
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
                case 'connection_changed':
                    return 'The event connection changed since it was inspected. Refresh its status before retrying.';
                case 'publication_stopped':
                    return 'Publication is stopped for this binding. Resume it before retrying event delivery.';
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
            clearPairingRecovery();
            refreshPairing.disabled = !pairingBinding.value;
            connectionConfigured = false;
            pausedConnection = null;
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

        function clearPairingRecovery() {
            pairingPending.hidden = true;
            pairingPending.open = false;
            pairingStatusPath.textContent = '';
            pairingRetryPath.textContent = '';
        }

        async function loadPairingStatus() {
            const binding = pairingBinding.value;
            const epoch = ++pairingRequestEpoch;
            pairingDetails.hidden = true;
            clearPairingRecovery();
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
                    !/^[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}$/.test(pairing.operation_id) ||
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
                if (pairing.state === 'pending') {
                    const path = `/api/v1/datasource/nextcloud-source-pairings/${encodeURIComponent(pairing.operation_id)}`;
                    pairingStatusPath.textContent = path;
                    pairingRetryPath.textContent = `${path}/retry`;
                    pairingPending.hidden = false;
                }
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
                clearPairingRecovery();
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
            retryConnection.hidden = pausedConnection === null;
            retryConnection.disabled = connectionBusy || !hasBinding || revokeArmed;
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
            pausedConnection = data.status === 'paused' ? {
                connection_id: data.connection_id,
                key_id: data.key_id,
                received_through_event_id: data.received_through_event_id,
            } : null;
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
            pausedConnection = null;
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

        function formatAge(seconds, emptyLabel = 'None retained') {
            if (!Number.isSafeInteger(seconds) || seconds < 0) {
                return emptyLabel;
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
                    typeof data.consumer_acknowledgement_available !== 'boolean' ||
                    !Number.isSafeInteger(data.outbox_pending_delivery_hints) ||
                    data.outbox_pending_delivery_hints < 0 ||
                    (data.oldest_outbox_pending_delivery_age_seconds !== null &&
                        (!Number.isSafeInteger(data.oldest_outbox_pending_delivery_age_seconds) ||
                         data.oldest_outbox_pending_delivery_age_seconds < 0)) ||
                    !Number.isSafeInteger(data.outbox_pending_application_hints) ||
                    data.outbox_pending_application_hints < 0 ||
                    (data.oldest_outbox_pending_application_age_seconds !== null &&
                        (!Number.isSafeInteger(data.oldest_outbox_pending_application_age_seconds) ||
                         data.oldest_outbox_pending_application_age_seconds < 0)) ||
                    !Array.isArray(data.event_connections) ||
                    data.event_connections.some((connection) =>
                        !connection || typeof connection.binding_id !== 'string' ||
                        typeof connection.status !== 'string' ||
                        !/^\d+$/.test(connection.received_through_event_id) ||
                        !/^\d+$/.test(connection.applied_through_event_id) ||
                        !Number.isSafeInteger(connection.outbox_pending_delivery_hints) ||
                        connection.outbox_pending_delivery_hints < 0 ||
                        !Number.isSafeInteger(connection.outbox_pending_application_hints) ||
                        connection.outbox_pending_application_hints < 0 ||
                        (connection.oldest_outbox_pending_application_age_seconds !== null &&
                            (!Number.isSafeInteger(connection.oldest_outbox_pending_application_age_seconds) ||
                             connection.oldest_outbox_pending_application_age_seconds < 0)) ||
                        typeof connection.last_error_code !== 'string' ||
                        typeof connection.applied_error_code !== 'string')) {
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
                document.getElementById('weknora-diagnostics-pending').textContent =
                    String(data.outbox_pending_delivery_hints);
                document.getElementById('weknora-diagnostics-pending-oldest').textContent =
                    formatAge(data.oldest_outbox_pending_delivery_age_seconds, 'None pending');
                document.getElementById('weknora-diagnostics-unapplied').textContent =
                    String(data.outbox_pending_application_hints);
                document.getElementById('weknora-diagnostics-unapplied-oldest').textContent =
                    formatAge(data.oldest_outbox_pending_application_age_seconds, 'None pending');
                const connectionRows = document.getElementById('weknora-diagnostics-connections');
                connectionRows.textContent = '';
                if (data.event_connections.length === 0) {
                    const row = document.createElement('tr');
                    const cell = document.createElement('td');
                    cell.colSpan = 11;
                    cell.textContent = 'No local event connection is configured.';
                    row.appendChild(cell);
                    connectionRows.appendChild(row);
                }
                for (const connection of data.event_connections) {
                    const values = [
                        connection.binding_id,
                        connection.status,
                        String(connection.outbox_pending_delivery_hints),
                        formatAge(connection.oldest_outbox_pending_delivery_age_seconds, 'None pending'),
                        String(connection.outbox_pending_application_hints),
                        formatAge(connection.oldest_outbox_pending_application_age_seconds, 'None pending'),
                        connection.received_through_event_id,
                        connection.applied_through_event_id,
                        connection.applied_checked_at > 0
                            ? new Date(connection.applied_checked_at * 1000).toLocaleString() : 'Never',
                        connection.last_error_code || 'None',
                        connection.applied_error_code || 'None',
                    ];
                    const row = document.createElement('tr');
                    for (const value of values) {
                        const cell = document.createElement('td');
                        cell.textContent = value;
                        row.appendChild(cell);
                    }
                    connectionRows.appendChild(row);
                }
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
        retryConnection.addEventListener('click', async () => {
            if (connectionBusy || pausedConnection === null) {
                return;
            }
            const binding = connectionBinding.value;
            const expected = pausedConnection;
            connectionBusy = true;
            ++connectionRequestEpoch;
            setConnectionControls();
            message(connectionMessage, 'Scheduling a retry of paused event delivery…', '');
            try {
                const data = await request('POST', connectionUrl(binding) + '/retry', expected);
                renderConnection(data, binding);
                message(connectionMessage,
                    'Delivery retry is queued. The receipt cursor is unchanged; refresh after the next worker run to inspect the result.',
                    'success');
            } catch (error) {
                message(connectionMessage, errorText(error), 'error');
                if (error.status === 409 || error.status === 404) {
                    pausedConnection = null;
                }
            } finally {
                connectionBusy = false;
                setConnectionControls();
            }
        });
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
                setConnectionControls();
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
                pausedConnection = null;
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
            setConnectionControls();
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

(function () {
    'use strict';

    function initConnectionSetup() {
        const form = document.getElementById('weknora-setup-form');
        if (!form) {
            return;
        }
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
        let discovered = [];
        let connectedSpaces = [];
        let editingVisibility = false;
        let pickerSequence = 0;
        const spacePickers = new WeakMap();

        function setMessage(value, kind) {
            message.textContent = value;
            if (kind) message.dataset.kind = kind;
            else delete message.dataset.kind;
        }

        function textNode(tag, className, value) {
            const element = document.createElement(tag);
            element.className = className;
            element.textContent = value;
            return element;
        }

        function principalName(type, id) {
            return `${type === 'group' ? '组' : '用户'} ${id}`;
        }

        function visibilityText(visibility) {
            const mode = visibility && visibility.mode;
            if (mode === 'admin') {
                return '仅 Nextcloud 管理员';
            }
            if (mode === 'restricted') {
                const groups = Array.isArray(visibility.groups) ? visibility.groups.length : 0;
                const users = Array.isArray(visibility.users) ? visibility.users.length : 0;
                return `${groups} 个组 · ${users} 个用户`;
            }
            return '所有 Nextcloud 用户';
        }

        function spaceCardHeader(space, enabled) {
            const header = document.createElement('div');
            header.className = 'weknora-admin__space-card-header';
            const heading = document.createElement('div');
            heading.className = 'weknora-admin__space-card-heading';
            heading.append(
                textNode('h4', 'weknora-admin__space-card-title',
                    space.tenant_name || space.name || `空间 ${space.tenant_id}`),
                textNode('span', 'weknora-admin__space-card-tenant',
                    `Tenant ${space.tenant_id}`),
            );
            const badge = textNode('span', 'weknora-admin__space-badge',
                enabled ? '已允许' : '未允许');
            badge.dataset.state = enabled ? 'enabled' : 'disabled';
            header.append(heading, badge);
            return header;
        }

        function spaceTemplate(space) {
            return space.processing_template_name
                || space.processing_template_id
                || '由 WeKnora 自动选择';
        }

        function summarySpaceCard(space) {
            const card = document.createElement('article');
            card.className = 'weknora-admin__space-card weknora-admin__space-card--summary';
            card.append(spaceCardHeader(space, true));

            const details = document.createElement('dl');
            details.className = 'weknora-admin__space-details';
            const detail = (label, value) => {
                details.append(
                    textNode('dt', '', label),
                    textNode('dd', '', value),
                );
            };
            detail('默认处理方案', spaceTemplate(space));
            detail('可见范围', visibilityText(space.visibility));
            card.append(details);
            return card;
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

        function renderCurrent(data) {
            current.hidden = false;
            current.replaceChildren();
            disconnect.hidden = !data.connected;
            if (!data.connected) {
                connectedSpaces = [];
                return;
            }
            connectedSpaces = Array.isArray(data.spaces) ? data.spaces : [];
            const title = document.createElement('p');
            title.className = 'weknora-admin__connected-title';
            title.textContent = `已连接：${data.origin}`;
            current.append(title);
            const list = document.createElement('div');
            list.className = 'weknora-admin__space-list';
            for (const space of data.spaces || []) {
                list.append(summarySpaceCard(space));
            }
            current.append(list);
            const editVisibility = document.createElement('button');
            editVisibility.type = 'button';
            editVisibility.className = 'button';
            editVisibility.textContent = '管理空间可见范围';
            editVisibility.addEventListener('click', () => {
                renderSpaces(connectedSpaces.map((space) => ({
                    ...space,
                    tenant_name: space.name || '',
                    eligible: true,
                })), true);
                setMessage('可调整每个空间的可见用户或组，不会重新授权或更换处理方案。');
                spacesPanel.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
            });
            current.append(editVisibility);
        }

        function initialPrincipals(visibility) {
            const out = [];
            for (const id of visibility && Array.isArray(visibility.groups) ? visibility.groups : []) {
                out.push({ type: 'group', id: String(id), name: '', label: principalName('group', id) });
            }
            for (const id of visibility && Array.isArray(visibility.users) ? visibility.users : []) {
                out.push({ type: 'user', id: String(id), name: '', label: principalName('user', id) });
            }
            return out;
        }

        function principalPicker(initial) {
            const root = document.createElement('div');
            root.className = 'weknora-admin__principal-picker';
            const control = document.createElement('div');
            control.className = 'weknora-admin__principal-control';
            const chips = document.createElement('div');
            chips.className = 'weknora-admin__principal-chips';
            const input = document.createElement('input');
            input.type = 'text';
            input.className = 'weknora-admin__principal-input';
            input.placeholder = '搜索用户或组名称、UID、组名…';
            input.autocomplete = 'off';
            input.spellcheck = false;
            input.setAttribute('role', 'combobox');
            input.setAttribute('aria-autocomplete', 'list');
            const results = document.createElement('div');
            results.className = 'weknora-admin__principal-results';
            results.id = `weknora-principal-results-${++pickerSequence}`;
            results.setAttribute('role', 'listbox');
            results.hidden = true;
            input.setAttribute('aria-controls', results.id);
            input.setAttribute('aria-expanded', 'false');
            control.append(chips, input);
            root.append(control, results);

            let selected = [];
            let items = [];
            let active = -1;
            let requestId = 0;
            let timer = null;

            const keyFor = (principal) => `${principal.type}\u0000${principal.id}`;

            const renderChips = () => {
                chips.replaceChildren();
                for (const principal of selected) {
                    const chip = document.createElement('span');
                    chip.className = 'weknora-admin__principal-chip';
                    chip.dataset.type = principal.type;
                    chip.append(
                        textNode('span', 'weknora-admin__principal-type',
                            principal.type === 'group' ? '组' : '用户'),
                        textNode('span', 'weknora-admin__principal-label',
                            principal.name || principal.label || principalName(principal.type, principal.id)),
                    );
                    const remove = textNode('button', 'weknora-admin__principal-remove', '×');
                    remove.type = 'button';
                    remove.setAttribute('aria-label',
                        `移除${principal.type === 'group' ? '组' : '用户'} ${principal.name || principal.id}`);
                    remove.addEventListener('click', () => {
                        selected = selected.filter((entry) => keyFor(entry) !== keyFor(principal));
                        renderChips();
                        input.focus();
                    });
                    chip.append(remove);
                    chips.append(chip);
                }
            };

            const setActive = (next) => {
                const options = [...results.querySelectorAll('[role="option"]')];
                if (!options.length) {
                    active = -1;
                    input.removeAttribute('aria-activedescendant');
                    return;
                }
                active = Math.max(0, Math.min(options.length - 1, next));
                for (let index = 0; index < options.length; index++) {
                    options[index].dataset.active = index === active ? 'true' : 'false';
                    options[index].setAttribute('aria-selected', index === active ? 'true' : 'false');
                }
                input.setAttribute('aria-activedescendant', options[active].id);
                options[active].scrollIntoView({ block: 'nearest' });
            };

            const choose = (principal) => {
                if (!principal || !['group', 'user'].includes(principal.type) ||
                    typeof principal.id !== 'string' || principal.id === '') {
                    return;
                }
                const candidate = {
                    type: principal.type,
                    id: principal.id,
                    name: String(principal.name || ''),
                    label: String(principal.label || principalName(principal.type, principal.id)),
                };
                if (selected.some((entry) => keyFor(entry) === keyFor(candidate))) {
                    return;
                }
                selected.push(candidate);
                input.value = '';
                active = -1;
                renderChips();
                results.hidden = true;
                input.setAttribute('aria-expanded', 'false');
                input.removeAttribute('aria-activedescendant');
                input.focus();
            };

            const renderResults = (choices, query) => {
                results.replaceChildren();
                items = choices.filter((choice) => choice && ['group', 'user'].includes(choice.type) &&
                    typeof choice.id === 'string' && choice.id !== '' &&
                    !selected.some((entry) => keyFor(entry) === keyFor(choice)));
                active = -1;
                if (!items.length) {
                    const empty = textNode('span', 'weknora-admin__principal-empty',
                        query ? '没有匹配的用户或组' : '输入姓名、UID、组名或显示名开始搜索');
                    results.append(empty);
                } else {
                    items.forEach((principal, index) => {
                        const option = document.createElement('button');
                        option.type = 'button';
                        option.className = 'weknora-admin__principal-result';
                        option.id = `${results.id}-option-${index}`;
                        option.setAttribute('role', 'option');
                        option.dataset.active = 'false';
                        option.setAttribute('aria-selected', 'false');
                        option.dataset.type = principal.type;
                        option.append(
                            textNode('span', 'weknora-admin__principal-type',
                                principal.type === 'group' ? '组' : '用户'),
                            textNode('span', 'weknora-admin__principal-result-label',
                                principal.label || principalName(principal.type, principal.id)),
                        );
                        option.addEventListener('mousedown', (event) => event.preventDefault());
                        option.addEventListener('click', () => choose(principal));
                        results.append(option);
                    });
                }
                results.hidden = false;
                input.setAttribute('aria-expanded', 'true');
            };

            const fallbackChoices = (data) => {
                if (Array.isArray(data.principals)) {
                    return data.principals;
                }
                return [
                    ...(Array.isArray(data.groups) ? data.groups.map((entry) =>
                        ({ ...entry, type: 'group' })) : []),
                    ...(Array.isArray(data.users) ? data.users.map((entry) =>
                        ({ ...entry, type: 'user' })) : []),
                ];
            };

            const load = async () => {
                const currentRequest = ++requestId;
                const query = input.value.trim();
                items = [];
                active = -1;
                results.replaceChildren(textNode('span', 'weknora-admin__principal-empty', '搜索中…'));
                results.hidden = false;
                input.setAttribute('aria-expanded', 'true');
                try {
                    const data = await request(
                        'GET',
                        `${baseUrl}/principals?query=${encodeURIComponent(query)}`,
                    );
                    if (currentRequest !== requestId) return;
                    renderResults(fallbackChoices(data), query);
                } catch (_) {
                    if (currentRequest !== requestId) return;
                    results.replaceChildren(textNode('span', 'weknora-admin__principal-empty',
                        '用户和组搜索暂不可用，请稍后重试'));
                    results.hidden = false;
                    input.setAttribute('aria-expanded', 'true');
                }
            };

            input.addEventListener('input', () => {
                clearTimeout(timer);
                timer = setTimeout(load, 180);
            });
            input.addEventListener('focus', () => {
                clearTimeout(timer);
                timer = setTimeout(load, 80);
            });
            input.addEventListener('blur', () => {
                setTimeout(() => {
                    results.hidden = true;
                    input.setAttribute('aria-expanded', 'false');
                    input.removeAttribute('aria-activedescendant');
                }, 140);
            });
            input.addEventListener('keydown', (event) => {
                if (event.key === 'ArrowDown') {
                    event.preventDefault();
                    if (results.hidden) void load();
                    else setActive(active + 1);
                } else if (event.key === 'ArrowUp') {
                    event.preventDefault();
                    setActive(active <= 0 ? items.length - 1 : active - 1);
                } else if (event.key === 'Enter' && !results.hidden) {
                    event.preventDefault();
                    choose(items[active >= 0 ? active : 0]);
                } else if (event.key === 'Escape') {
                    results.hidden = true;
                    input.setAttribute('aria-expanded', 'false');
                    input.removeAttribute('aria-activedescendant');
                } else if (event.key === 'Backspace' && input.value === '' && selected.length) {
                    selected.pop();
                    renderChips();
                }
            });

            const setValues = (values) => {
                selected = [];
                for (const principal of values) {
                    if (!principal || !['group', 'user'].includes(principal.type) ||
                        typeof principal.id !== 'string' || principal.id === '' ||
                        selected.some((entry) => keyFor(entry) === keyFor(principal))) {
                        continue;
                    }
                    selected.push(principal);
                }
                renderChips();
            };

            setValues(initial || []);
            control.addEventListener('click', (event) => {
                if (!event.target.closest('button, input')) {
                    input.focus();
                }
            });
            return {
                root,
                input,
                setValues,
                values: () => selected.slice(),
                isEmpty: () => selected.length === 0,
            };
        }

        async function loadStatus() {
            try {
                const data = await request('GET', baseUrl);
                renderCurrent(data);
                if (data.connected && data.origin) origin.value = data.origin;
            } catch (_) {
                setMessage('连接状态暂不可用。', 'error');
            }
        }

        function renderSpaces(spaces, visibilityOnly = false) {
            discovered = spaces;
            editingVisibility = visibilityOnly;
            spaceList.replaceChildren();
            spacesPanel.hidden = false;
            current.hidden = spaces.length > 0;
            save.hidden = spaces.length === 0;
            save.textContent = visibilityOnly ? '保存可见范围' : '保存连接';
            const updateCount = () => {
                const cards = [...spaceList.querySelectorAll('.weknora-admin__space-card')];
                const enabled = cards.filter((candidate) => candidate.classList.contains('is-selected')).length;
                spaceCount.textContent = visibilityOnly
                    ? `${cards.length} 个空间`
                    : `${enabled} / ${cards.length} 个空间已允许`;
            };
            for (const space of spaces) {
                const available = visibilityOnly || space.eligible === true;
                let allowed = available;
                const card = document.createElement('article');
                card.className = 'weknora-admin__space-card';
                card.dataset.tenantId = String(space.tenant_id);
                card.classList.toggle('is-unavailable', !available);
                card.classList.toggle('is-selected', allowed);

                const header = spaceCardHeader(space, allowed);
                const badge = header.querySelector('.weknora-admin__space-badge');
                const actions = document.createElement('div');
                actions.className = 'weknora-admin__space-card-actions';
                let checkbox = null;
                if (!visibilityOnly) {
                    const allow = document.createElement('label');
                    allow.className = 'weknora-admin__space-allow';
                    checkbox = document.createElement('input');
                    checkbox.type = 'checkbox';
                    checkbox.value = String(space.tenant_id);
                    checkbox.checked = allowed;
                    checkbox.disabled = !available;
                    allow.append(checkbox, textNode('span', '', '允许目录使用'));
                    actions.append(allow);
                }
                badge.remove();
                actions.append(badge);
                header.append(actions);
                card.append(header);

                const details = document.createElement('dl');
                details.className = 'weknora-admin__space-details';
                details.append(
                    textNode('dt', '', '默认处理方案'),
                    textNode('dd', '', spaceTemplate(space)),
                );
                card.append(details);

                if (!available) {
                    const reason = space.reason === 'no_processing_template'
                        ? 'WeKnora 未返回可用的默认处理方案'
                        : '当前连接密钥无权管理此空间';
                    card.append(textNode('p', 'weknora-admin__space-reason', reason));
                }

                const access = document.createElement('div');
                access.className = 'weknora-admin__space-access';
                const modeLabel = textNode('label', 'weknora-admin__space-field', '可见范围');
                const mode = document.createElement('select');
                mode.className = 'weknora-admin__visibility-mode';
                for (const [value, textContent] of [
                    ['all', '所有 Nextcloud 用户'],
                    ['restricted', '指定用户和组'],
                    ['admin', '仅 Nextcloud 管理员'],
                ]) {
                    const option = document.createElement('option');
                    option.value = value;
                    option.textContent = textContent;
                    mode.append(option);
                }
                modeLabel.append(mode);
                const initialVisibility = space.visibility || {};
                if (['all', 'restricted', 'admin'].includes(initialVisibility.mode)) {
                    mode.value = initialVisibility.mode;
                }

                const principalsLabel = textNode('label', 'weknora-admin__space-field',
                    '可访问此空间的 Nextcloud 用户或组');
                const picker = principalPicker(initialPrincipals(initialVisibility));
                principalsLabel.append(picker.root);
                principalsLabel.hidden = mode.value !== 'restricted';
                mode.addEventListener('change', () => {
                    principalsLabel.hidden = mode.value !== 'restricted';
                    if (mode.value === 'restricted') {
                        picker.input.focus();
                    }
                });
                modeLabel.append(mode);
                access.append(modeLabel, principalsLabel);
                card.append(access);
                spacePickers.set(card, picker);

                const updateAvailability = () => {
                    allowed = visibilityOnly || checkbox === null || checkbox.checked;
                    card.classList.toggle('is-selected', allowed);
                    badge.dataset.state = allowed ? 'enabled' : 'disabled';
                    badge.textContent = allowed ? '已允许' : '未允许';
                    access.hidden = !allowed;
                };
                if (checkbox !== null) {
                    checkbox.addEventListener('change', updateAvailability);
                }
                checkbox?.addEventListener('change', updateCount);
                updateAvailability();
                spaceList.append(card);
            }
            updateCount();
            if (!spaces.length) {
                const empty = document.createElement('p');
                empty.className = 'weknora-admin__space-list-empty';
                empty.textContent = '没有发现可授权的空间。请确认连接密钥属于平台管理员或空间 Owner/Admin。';
                spaceList.append(empty);
                spaceCount.textContent = '0 个空间';
            }
        }

        form.addEventListener('submit', async (event) => {
            event.preventDefault();
            discover.disabled = true;
            setMessage('正在读取空间…');
            try {
                const data = await request('POST', `${baseUrl}/discover`, {
                    origin: origin.value.trim(),
                    token: token.value,
                });
                renderSpaces(Array.isArray(data.spaces) ? data.spaces : []);
                setMessage('已读取空间。勾选需要允许 Nextcloud 使用的空间后保存。');
            } catch (error) {
                spacesPanel.hidden = true;
                current.hidden = false;
                setMessage(error.status === 403
                    ? '连接被拒绝。请确认密钥有效且具有空间管理权限。'
                    : '无法读取 WeKnora 空间，请检查地址、证书和网络。', 'error');
            } finally {
                discover.disabled = false;
            }
        });

        save.addEventListener('click', async () => {
            const spaces = [];
            for (const card of spaceList.querySelectorAll('.weknora-admin__space-card')) {
                const checkbox = card.querySelector('.weknora-admin__space-allow input[type=checkbox]');
                if (checkbox !== null && !checkbox.checked) {
                    continue;
                }
                const mode = card.querySelector('.weknora-admin__visibility-mode').value;
                const picker = spacePickers.get(card);
                const values = mode === 'restricted' && picker ? picker.values() : [];
                const groups = values.filter((principal) => principal.type === 'group')
                    .map((principal) => principal.id);
                const users = values.filter((principal) => principal.type === 'user')
                    .map((principal) => principal.id);
                if (mode === 'restricted' && !groups.length && !users.length) {
                    setMessage('指定可见范围时，至少选择一个 Nextcloud 用户或组。', 'error');
                    if (picker) picker.input.focus();
                    return;
                }
                spaces.push({
                    tenant_id: card.dataset.tenantId,
                    visibility: { mode, groups, users },
                });
            }
            if (!spaces.length) {
                setMessage('至少选择一个空间。', 'error');
                return;
            }
            save.disabled = true;
            const original = save.textContent;
            save.textContent = '正在保存…';
            try {
                const data = await request(
                    'POST',
                    editingVisibility ? `${baseUrl}/visibility` : baseUrl,
                    editingVisibility ? { spaces } : {
                        origin: origin.value.trim(),
                        token: token.value,
                        spaces,
                    },
                );
                if (!editingVisibility) token.value = '';
                renderCurrent(data);
                spacesPanel.hidden = true;
                setMessage(editingVisibility
                    ? '空间可见范围已保存，现有目录绑定保持不变。'
                    : '连接已保存。现在可以在 Nextcloud 目录的 WeKnora 标签中选择空间和知识库。');
            } catch (error) {
                setMessage(error.status === 400
                    ? '连接配置无效，请确认指定的 Nextcloud 用户和组 ID 存在。'
                    : error.status === 409
                        ? '空间授权未完成，请检查该空间是否有可用的处理方案。'
                        : '连接未保存，请检查密钥和管理员权限。', 'error');
            } finally {
                save.disabled = false;
                save.textContent = original;
            }
        });

        disconnect.addEventListener('click', async () => {
            if (!window.confirm('断开连接会停用当前空间的连接凭据，已绑定的目录和知识库不会被删除。继续？')) {
                return;
            }
            disconnect.disabled = true;
            try {
                const data = await request('DELETE', baseUrl);
                renderCurrent(data);
                spacesPanel.hidden = true;
                setMessage('连接已断开。');
            } catch (_) {
                setMessage('断开失败，请稍后重试。', 'error');
            } finally {
                disconnect.disabled = false;
            }
        });

        loadStatus();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initConnectionSetup, { once: true });
    } else {
        initConnectionSetup();
    }
}());

(function () {
    'use strict';
    function initDirectoryRules() {
        const form = document.getElementById('weknora-directory-rule-form');
        if (!form) return;
        const message = document.getElementById('weknora-directory-rule-message');
        const list = document.getElementById('weknora-directory-rules-list');
        const url = window.OC.generateUrl('/apps/integration_weknora/api/v1/admin/directory-rules');
        async function request(method, body) {
            const response = await fetch(url, { method, credentials: 'same-origin', cache: 'no-store',
                headers: { Accept: 'application/json', 'Content-Type': 'application/json',
                    'X-Requested-With': 'XMLHttpRequest', requesttoken: window.OC.requestToken || '' },
                body: body === undefined ? undefined : JSON.stringify(body) });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || 'rules_unavailable');
            return data;
        }
        async function refresh() {
            try { const data = await request('GET'); list.textContent = JSON.stringify(data.rules, null, 2); }
            catch (_) { message.textContent = '规则读取暂不可用。'; }
        }
        form.addEventListener('submit', async function (event) {
            event.preventDefault();
            const fields = new FormData(form);
            const rule = {};
            for (const name of ['id', 'owner_uid', 'tenant_id', 'processing_template_id', 'weknora_origin', 'base_url', 'discovery', 'automation_uid']) {
                rule[name] = String(fields.get(name) || '').trim();
            }
            rule.container_file_id = Number(fields.get('container_file_id'));
            rule.delegates = String(fields.get('delegates')).split(',').map(uid => uid.trim()).filter(Boolean);
            rule.auto_enabled = fields.get('auto_enabled') === 'on';
            const targets = String(fields.get('targets') || '').trim();
            if (targets) {
                try { rule.targets = JSON.parse(targets); }
                catch (_) { message.textContent = '工作空间 JSON 格式无效。'; return; }
            }
            const revision = Number(fields.get('expected_revision'));
            const submit = form.querySelector('[type=submit]'); submit.disabled = true;
            try {
                const data = await request('POST', { rule, expected_revision: revision });
                form.elements.expected_revision.value = data.rule.revision;
                message.textContent = '规则已保存。'; await refresh();
            } catch (_) { message.textContent = '规则未保存。请检查当前版本、目录范围、管理委派和允许的地址。'; }
            finally { submit.disabled = false; }
        });
        document.getElementById('weknora-directory-rules-refresh').addEventListener('click', refresh);
        refresh();
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initDirectoryRules, { once: true });
    else initDirectoryRules();
}());
