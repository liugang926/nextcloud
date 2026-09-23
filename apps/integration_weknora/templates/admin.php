<?php

declare(strict_types=1);

script('integration_weknora', 'weknora-admin');
style('integration_weknora', 'weknora-admin');
?>
<section id="weknora-admin" class="section weknora-admin" data-bindings-url="<?php p($_['bindingsUrl']); ?>" data-diagnostics-url="<?php p($_['diagnosticsUrl']); ?>">
    <h2>WeKnora publication</h2>
    <p>Choose a dedicated Nextcloud folder as the source of files eligible for WeKnora indexing.</p>

    <div class="weknora-admin__notice" role="note">
        <h3>Automatic publication scope</h3>
        <p>A binding covers readable files recursively inside the selected child folder, including new and updated files. Withdrawing a file ID excludes it from this app's manifest and content API until an administrator explicitly republishes it. A folder binding does not itself make content searchable in WeKnora.</p>
        <p>Stop publication closes the binding's source read and authorization gates without deleting its files, binding ID, machine keys, or file exclusions. Resume checks the current root and asks the connector to reconcile again. Existing WeKnora copies require its retrieval guard and later cleanup; this page cannot verify that cleanup.</p>
        <p>Current limits: bindings must share one owner account and their roots cannot overlap. This settings page can configure and inspect local event delivery, but it does not configure WeKnora knowledge base mapping or show WeKnora synchronization, parsing, indexing, or cleanup progress. End-to-end audience and ACL enforcement and in-Nextcloud AI chat are not complete. Withdrawal immediately blocks this app's read API; removal of an already indexed WeKnora copy still requires connector reconciliation and WeKnora retrieval controls. Use a verified dedicated publication folder for pilot data only.</p>
    </div>

    <div class="weknora-admin__panel">
        <div class="weknora-admin__panel-heading">
            <h3>Source diagnostics</h3>
            <button type="button" class="button" id="weknora-refresh-diagnostics">Refresh</button>
        </div>
        <p id="weknora-diagnostics-message" class="weknora-admin__message" role="status" aria-live="polite">Loading source diagnostics…</p>
        <div id="weknora-diagnostics-values" hidden>
            <p>Binding roots: <strong id="weknora-diagnostics-roots"></strong></p>
            <p>Retained change hints: <strong id="weknora-diagnostics-hints"></strong></p>
            <p>Oldest retained hint age: <strong id="weknora-diagnostics-oldest"></strong></p>
            <p>Latest change hint: <strong id="weknora-diagnostics-newest"></strong></p>
            <p>Explicit file withdrawals: <strong id="weknora-diagnostics-withdrawals"></strong></p>
        </div>
        <p>Retained hints are historical records, not an undelivered backlog. The event connection below shows the latest durable receipt acknowledged by WeKnora, which does not mean the change was synchronized, parsed, or indexed.</p>
    </div>

    <div class="weknora-admin__panel">
        <div class="weknora-admin__panel-heading">
            <h3>Folder bindings</h3>
            <button type="button" class="button" id="weknora-refresh-bindings">Refresh</button>
        </div>
        <p>Enter the owning user's UID and the Nextcloud file ID of a child folder. Administrators can obtain the internal ID from the folder's WebDAV <code>oc:fileid</code> property. Saving an existing ID can rename it; changing its owner or root requires a new binding.</p>
        <form id="weknora-binding-form" autocomplete="off">
            <div class="weknora-admin__fields">
                <label>Binding ID <input id="weknora-binding-id" name="id" type="text" required maxlength="128" pattern="[A-Za-z0-9_-]+" placeholder="department-docs"></label>
                <label>Display name <input id="weknora-binding-name" name="name" type="text" required maxlength="255" placeholder="Department documents"></label>
                <label>Owner UID <input id="weknora-owner-uid" name="owner_uid" type="text" required placeholder="publication-owner"></label>
                <label>Root folder file ID <input id="weknora-root-file-id" name="root_file_id" type="text" required inputmode="numeric" pattern="[1-9][0-9]*" placeholder="123"></label>
            </div>
            <button type="submit" class="button primary" id="weknora-save-binding">Save binding</button>
            <span id="weknora-binding-message" class="weknora-admin__message" role="status" aria-live="polite"></span>
        </form>
        <div class="weknora-admin__table-scroll">
            <table class="weknora-admin__table" aria-label="Configured WeKnora folder bindings">
                <thead><tr><th>Binding ID</th><th>Name</th><th>Owner UID</th><th>Root folder ID</th><th>Publication</th><th>Actions</th></tr></thead>
                <tbody id="weknora-bindings-list"><tr><td colspan="6">Loading bindings…</td></tr></tbody>
            </table>
        </div>
    </div>

    <div class="weknora-admin__panel">
        <div class="weknora-admin__panel-heading">
            <h3>Event delivery connection</h3>
            <button type="button" class="button" id="weknora-refresh-connection">Refresh status</button>
        </div>
        <p>Select a binding to inspect its local sender. Pair the matching Nextcloud data source in WeKnora first, then install its one-time credential here. The status below covers delivery from Nextcloud to WeKnora's durable event inbox; it does not show WeKnora synchronization or indexing.</p>
        <div class="weknora-admin__fields">
            <label>Binding <select id="weknora-connection-binding"><option value="">Choose a binding</option></select></label>
        </div>
        <p id="weknora-connection-message" class="weknora-admin__message" role="status" aria-live="polite"></p>
        <dl id="weknora-connection-details" class="weknora-admin__details" hidden>
            <dt>Local sender</dt><dd id="weknora-connection-status"></dd>
            <dt>Connection ID</dt><dd id="weknora-connection-id"></dd>
            <dt>Key ID</dt><dd id="weknora-connection-key-id"></dd>
            <dt>Receiver URL</dt><dd id="weknora-connection-receiver"></dd>
            <dt>Durably received through event ID</dt><dd id="weknora-connection-received"></dd>
            <dt>Local delivery attempts since last receipt</dt><dd id="weknora-connection-attempts"></dd>
            <dt>Next local delivery attempt</dt><dd id="weknora-connection-next-attempt"></dd>
            <dt>Last local delivery error code</dt><dd id="weknora-connection-error"></dd>
        </dl>
        <form id="weknora-connection-form" autocomplete="off">
            <div class="weknora-admin__fields">
                <label>WeKnora origin
                    <input id="weknora-connection-origin" type="url" required autocomplete="off" spellcheck="false" placeholder="https://weknora.example.com" aria-describedby="weknora-connection-help">
                </label>
                <label>One-time Pair or Rotate JSON
                    <input id="weknora-connection-credential" type="password" required autocomplete="off" spellcheck="false" aria-describedby="weknora-connection-help">
                </label>
            </div>
            <p id="weknora-connection-help">Use the exact WeKnora origin approved by the server administrator. Paste the full one-time JSON response into the hidden field. Its secret is cleared from this page immediately after submission; it cannot be retrieved later.</p>
            <div class="weknora-admin__actions">
                <button type="submit" class="button primary" id="weknora-save-connection">Install credential</button>
                <button type="button" class="button" id="weknora-revoke-connection" hidden>Revoke local connection</button>
                <button type="button" class="button" id="weknora-cancel-revoke" hidden>Cancel</button>
            </div>
        </form>
        <p>Rotation requires the same connection ID and receiver URL with a new key ID. Local revocation stops sending and removes the local credential; separately revoke the connection in WeKnora.</p>
    </div>

    <div class="weknora-admin__panel">
        <h3>File publication decision</h3>
        <p>Use a Nextcloud file ID within a binding to inspect its publication eligibility. Withdrawal leaves the original file in Nextcloud and may reserve an exclusion even when that file has moved or been deleted. Republish requires the file to be currently readable in the bound folder.</p>
        <form id="weknora-publication-form" autocomplete="off">
            <div class="weknora-admin__fields">
                <label>Binding <select id="weknora-publication-binding" required><option value="">Choose a binding</option></select></label>
                <label>File ID <input id="weknora-publication-file-id" type="text" required inputmode="numeric" pattern="[1-9][0-9]*" placeholder="456"></label>
            </div>
            <div class="weknora-admin__actions">
                <button type="submit" class="button" id="weknora-check-state">Check state</button>
                <button type="button" class="button" id="weknora-withdraw">Withdraw and exclude</button>
                <button type="button" class="button" id="weknora-cancel-withdrawal" hidden>Cancel</button>
                <button type="button" class="button" id="weknora-republish">Republish</button>
            </div>
            <p id="weknora-publication-message" class="weknora-admin__message" role="status" aria-live="polite"></p>
        </form>
    </div>
</section>
