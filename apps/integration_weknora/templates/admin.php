<?php

declare(strict_types=1);

script('integration_weknora', 'weknora-admin');
style('integration_weknora', 'weknora-admin');
?>
<section id="weknora-admin" class="section weknora-admin" data-bindings-url="<?php p($_['bindingsUrl']); ?>" data-diagnostics-url="<?php p($_['diagnosticsUrl']); ?>">
    <h2>WeKnora publication</h2>
    <p>Choose a dedicated Nextcloud folder as the source of files eligible for WeKnora indexing.</p>

    <div class="weknora-admin__panel" id="weknora-connection-setup">
        <h3>连接 WeKnora</h3>
        <p>首次连接只需要 WeKnora 地址和管理员连接密钥。密钥仅用于发现和授权空间，不会保存到 Nextcloud。</p>
        <form id="weknora-setup-form" autocomplete="off">
            <div class="weknora-admin__fields">
                <label>WeKnora 地址
                    <input id="weknora-setup-origin" name="origin" type="url" required
                        placeholder="https://weknora.example.com" spellcheck="false">
                </label>
                <label>管理员连接密钥（仅首次连接使用）
                    <input id="weknora-setup-token" name="token" type="password" required
                        autocomplete="off" spellcheck="false">
                </label>
            </div>
            <div class="weknora-admin__actions">
                <button type="submit" class="button primary" id="weknora-setup-discover">发现空间</button>
                <button type="button" class="button" id="weknora-setup-disconnect" hidden>断开连接</button>
            </div>
        </form>
        <p>连接密钥支持 WeKnora 平台 API Key（需要空间读取/管理能力）或已登录管理员令牌。空间默认处理方案由 WeKnora 选择，目录用户不需要填写模板 ID。</p>
        <p id="weknora-setup-message" class="weknora-admin__message" role="status" aria-live="polite"></p>
        <div id="weknora-setup-spaces" hidden>
            <div class="weknora-admin__space-section-heading">
                <div>
                    <h4>允许 Nextcloud 目录使用的空间</h4>
                    <p>按空间逐项设置。一个搜索框可同时查找 Nextcloud 用户和组；可访问该空间的目录管理者可在目录中绑定已有知识库或新建知识库。</p>
                </div>
                <span class="weknora-admin__space-count" id="weknora-setup-space-count"></span>
            </div>
            <div id="weknora-setup-space-list" class="weknora-admin__space-list"></div>
            <button type="button" class="button primary" id="weknora-setup-save">保存连接</button>
        </div>
        <div id="weknora-setup-current"></div>
    </div>

    <details class="weknora-admin__panel">
        <summary><strong>高级：目录规则</strong></summary>
        <p>普通连接和目录绑定会自动生成内部规则。只有排障或迁移既有配置时才需要编辑下面的字段。</p>
    <div class="weknora-admin__panel">
        <h3>目录自动关联规则</h3>
        <p>登记发布范围、已有工作空间及明确管理委派后，员工可在目录详情中创建或关联专用知识库。自动发现只处理容器的直接子目录。</p>
        <form id="weknora-directory-rule-form" autocomplete="off">
            <div class="weknora-admin__fields">
                <label>规则 ID <input name="id" required pattern="[A-Za-z0-9_-]{1,128}"></label>
                <label>当前规则版本 <input name="expected_revision" type="number" min="0" value="0" required></label>
                <label>来源账户 UID <input name="owner_uid" maxlength="64" required></label>
                <label>容器目录 ID <input name="container_file_id" type="number" min="1" required></label>
                <label>已有工作空间 ID <input name="tenant_id" inputmode="numeric" required></label>
                <label>处理模板知识库 ID <input name="processing_template_id" required></label>
                <label>允许的工作空间与凭据 ID（可选 JSON） <textarea name="targets" rows="4" placeholder='[{"tenant_id":"7","processing_template_id":"template-kb","credential_id":"research-space"}]'></textarea></label>
                <label>WeKnora 地址 <input name="weknora_origin" type="url" placeholder="https://weknora.example.com" required></label>
                <label>Nextcloud 地址 <input name="base_url" type="url" placeholder="https://nextcloud.example.com" required></label>
                <label>目录发现 <select name="discovery"><option value="explicit">范围内显式关联</option><option value="direct_children">容器直接子目录</option></select></label>
                <label>管理委派 UID（逗号分隔） <input name="delegates" required></label>
                <label>自动操作账户 UID <input name="automation_uid" maxlength="64"></label>
                <label><input name="auto_enabled" type="checkbox">启用自动发现</label>
            </div>
            <button type="submit" class="button primary">保存规则</button>
            <button type="button" id="weknora-directory-rules-refresh" class="button">刷新规则</button>
            <p id="weknora-directory-rule-message" role="status" aria-live="polite"></p>
        </form>
        <pre id="weknora-directory-rules-list"></pre>
        <p>服务端须预置已限定范围的开通凭据与允许的 WeKnora 地址。员工不接触此凭据。暂停同步保留当前权限检查；停止发布立即关闭来源读取。</p>
    </div>
    </details>

    <div class="weknora-admin__notice" role="note">
        <h3>Automatic publication scope</h3>
        <p>A binding covers readable files recursively inside the selected child folder, including new and updated files. Withdrawing a file ID excludes it from this app's manifest and content API until an administrator explicitly republishes it. A folder binding does not itself make content searchable in WeKnora.</p>
        <p>Stop publication closes the binding's source read and authorization gates without deleting its files, binding ID, machine keys, or file exclusions. Resume checks the current root and asks the connector to reconcile again. Existing WeKnora copies require its retrieval guard and later cleanup; this page cannot verify that cleanup.</p>
        <p>Publication roots cannot overlap across any owner view. Local home folders and the verified Team Folders adapter are supported; unknown mounts, advanced ACL and descendant shares pause onboarding. A connection's applied watermark is distinct from current per-file processing status. Source reads and authorization continue to verify current permissions, including when synchronization is paused.</p>
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
            <p>Verified applied status from an active connection: <strong id="weknora-diagnostics-applied-ack"></strong></p>
            <p>Outbox hints awaiting a durable receipt: <strong id="weknora-diagnostics-pending"></strong></p>
            <p>Oldest hint awaiting a durable receipt: <strong id="weknora-diagnostics-pending-oldest"></strong></p>
            <p>Received hints awaiting verified application: <strong id="weknora-diagnostics-unapplied"></strong></p>
            <p>Oldest received hint awaiting verified application: <strong id="weknora-diagnostics-unapplied-oldest"></strong></p>
            <div class="weknora-admin__table-scroll">
                <table class="weknora-admin__table" aria-label="Local event delivery by binding">
                    <thead><tr><th>Binding</th><th>Sender</th><th>Awaiting receipt</th><th>Oldest awaiting receipt</th><th>Awaiting application</th><th>Oldest awaiting application</th><th>Received through ID</th><th>Applied through ID</th><th>Applied check</th><th>Delivery error</th><th>Applied status error</th></tr></thead>
                    <tbody id="weknora-diagnostics-connections"></tbody>
                </table>
            </div>
        </div>
        <p>Pending counts cover only configured local senders, including paused senders. Receipt counts exclude already received hints; application counts include only retained hints above the last verified applied watermark and at or below the durable receipt. Both exclude bindings without an event connection. These source-side figures are not WeKnora's task queue, synchronization delay, parsing health, or per-file publication state. A received or applied watermark alone does not make a file ready.</p>
    </div>

    <details class="weknora-admin__panel">
        <summary><strong>高级：运维与配对</strong></summary>
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
            <h3>Source pairing</h3>
            <button type="button" class="button" id="weknora-refresh-pairing">Refresh status</button>
        </div>
        <p>A dedicated WeKnora knowledge base is paired to one binding through the operator workflow. This is the latest state stored in Nextcloud; confirm the matching operation and data source in WeKnora before starting synchronization.</p>
        <div class="weknora-admin__fields">
            <label>Binding <select id="weknora-pairing-binding"><option value="">Choose a binding</option></select></label>
        </div>
        <p id="weknora-pairing-message" class="weknora-admin__message" role="status" aria-live="polite"></p>
        <dl id="weknora-pairing-details" class="weknora-admin__details" hidden>
            <dt>Local state</dt><dd id="weknora-pairing-state"></dd>
            <dt>Operation ID</dt><dd id="weknora-pairing-operation"></dd>
            <dt>Nextcloud instance</dt><dd id="weknora-pairing-instance"></dd>
            <dt>WeKnora tenant ID</dt><dd id="weknora-pairing-tenant"></dd>
            <dt>Knowledge base ID</dt><dd id="weknora-pairing-kb"></dd>
            <dt>Data source ID</dt><dd id="weknora-pairing-source"></dd>
            <dt>Machine key ID</dt><dd id="weknora-pairing-key"></dd>
            <dt>Publication epoch at prepare</dt><dd id="weknora-pairing-epoch"></dd>
        </dl>
        <details id="weknora-pairing-pending" hidden>
            <summary>Inspect or retry this pending operation in WeKnora</summary>
            <p>Use a WeKnora tenant administrator with edit access to the dedicated knowledge base. Inspect the same operation before retrying it. WeKnora uses its stored credential for retry; neither status nor retry returns the one-time token.</p>
            <p>Inspect: <code>GET <span id="weknora-pairing-status-path"></span></code></p>
            <p>Retry: <code>POST <span id="weknora-pairing-retry-path"></span></code></p>
            <p>Send these requests to the approved WeKnora origin through its administrator workflow, then refresh the state in both services. This Nextcloud session cannot submit WeKnora administrator requests. If WeKnora has no matching operation, resolve the pending intent through the documented abort workflow before preparing another one.</p>
        </details>
    </div>

    <div class="weknora-admin__panel">
        <div class="weknora-admin__panel-heading">
            <h3>Event delivery connection</h3>
            <button type="button" class="button" id="weknora-refresh-connection">Refresh status</button>
        </div>
        <p>Select a binding to inspect its local sender. Pair the matching Nextcloud data source in WeKnora first, then install its one-time credential here. The received watermark covers durable inbox acceptance; the separately verified applied watermark covers completion of the connection's hints. Neither is a per-file ready result.</p>
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
            <dt>Verified applied through event ID</dt><dd id="weknora-connection-applied"></dd>
            <dt>Last applied status check</dt><dd id="weknora-connection-applied-checked"></dd>
            <dt>Applied status error code</dt><dd id="weknora-connection-applied-error"></dd>
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
                <button type="button" class="button" id="weknora-retry-connection" hidden>Retry paused delivery</button>
                <button type="button" class="button" id="weknora-revoke-connection" hidden>Revoke local connection</button>
                <button type="button" class="button" id="weknora-cancel-revoke" hidden>Cancel</button>
            </div>
        </form>
        <p>Received means durable inbox acceptance. Applied means the last signed WeKnora status verified by Nextcloud; it may lag while synchronization, parsing or deletion is unfinished. Inspect the error and correct its cause before retrying paused delivery. Retry retains the receipt cursor and attempts the next unsent event on the following worker run. Rotation requires the same connection ID and receiver URL with a new key ID. Local revocation stops sending and removes the local credential; separately revoke the connection in WeKnora.</p>
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
    </details>
</section>
