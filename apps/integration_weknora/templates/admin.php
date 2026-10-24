<?php

declare(strict_types=1);

script('integration_weknora', 'weknora-admin-summary');
script('integration_weknora', 'weknora-admin');
style('integration_weknora', 'weknora-admin');
?>
<section id="weknora-admin" class="section weknora-admin">
    <h2>WeKnora 连接</h2>
    <p>连接用于发现并授权本 Nextcloud 实例可使用的 WeKnora 空间。目录用户只按名称选择空间和知识库。</p>

    <div class="weknora-admin__panel" id="weknora-connection-setup">
        <h3>连接与授权</h3>
        <p>管理员连接密钥仅在本次发现和授权期间使用，不写入日志或浏览器存储。生产环境只接受运维预批准的 WeKnora 地址。</p>
        <form id="weknora-setup-form" autocomplete="off">
            <div class="weknora-admin__fields">
                <label>WeKnora 地址
                    <input id="weknora-setup-origin" name="origin" type="url" required
                        placeholder="https://weknora.example.com" spellcheck="false">
                </label>
                <label>一次性管理员连接密钥
                    <input id="weknora-setup-token" name="token" type="password" required
                        autocomplete="off" spellcheck="false">
                </label>
            </div>
            <div class="weknora-admin__actions">
                <button type="submit" class="button primary" id="weknora-setup-discover">发现空间</button>
                <button type="button" class="button" id="weknora-setup-disconnect" hidden>
                    断开管理连接（不停止已有来源）
                </button>
            </div>
        </form>
        <p>连接密钥由 WeKnora 空间 Owner 或 Admin 批准。空间允许范围只决定新目录可选择的候选，不会自动停止既有目录同步。</p>
        <p id="weknora-setup-message" class="weknora-admin__message" role="status" aria-live="polite"></p>

        <div id="weknora-setup-spaces" hidden>
            <div class="weknora-admin__space-section-heading">
                <div>
                    <h4>允许 Nextcloud 使用的空间</h4>
                    <p>按空间设置允许哪些 Nextcloud 用户或组创建目录配置。收紧范围不会停止已有发布来源。</p>
                </div>
                <span class="weknora-admin__space-count" id="weknora-setup-space-count"></span>
            </div>
            <div id="weknora-setup-space-list" class="weknora-admin__space-list"></div>
            <button type="button" class="button primary" id="weknora-setup-save">保存连接</button>
        </div>

        <div id="weknora-setup-current"></div>
    </div>
</section>
