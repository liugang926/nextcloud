#!/usr/bin/env node
// Run the real Files -> WeKnora question flow against one owned loopback fixture.

const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { execFileSync } = require('node:child_process')

function argumentsForRun(argv) {
    const values = {}
    for (let i = 0; i < argv.length; i += 2) {
        const key = argv[i]
        if (!key?.startsWith('--') || !argv[i + 1]) {
            throw new Error('expected --scratch, --evidence-dir and optional browser paths')
        }
        values[key.slice(2)] = argv[i + 1]
    }
    if (!values.scratch || !values['evidence-dir']) {
        throw new Error('--scratch and --evidence-dir are required')
    }
    return values
}

function ownedFixture(input) {
    const scratch = fs.realpathSync(input)
    const temp = fs.realpathSync(os.tmpdir())
    const relative = path.relative(temp, scratch)
    assert(relative && !relative.startsWith('..' + path.sep) && relative !== '..',
        'scratch must be inside the host temporary directory')
    const read = name => JSON.parse(fs.readFileSync(path.join(scratch, name), 'utf8'))
    const state = read('state.json')
    const compose = read('compose.yaml')
    const runtime = read('runtime.json')
    const passwords = read('passwords.json')
    assert.equal(state.marker, 'nextcloud-weknora-synthetic-ldap-v1')
    assert.match(state.project, /^nc-synldap-[0-9a-f]{8}$/)
    assert.match(state.weknora_ui_image_id || '', /^sha256:[0-9a-f]{64}$/)
    for (const key of ['nextcloud', 'weknora', 'weknora_ui']) {
        assert(Number.isInteger(state.ports[key]) && state.ports[key] > 1024)
    }
    assert.equal(compose.name, state.project)
    assert.equal(compose.services['wk-ui'].image, state.weknora_ui_image)
    assert.deepEqual(compose.services['wk-ui'].ports,
        [`127.0.0.1:${state.ports.weknora_ui}:80`])
    assert.deepEqual(compose.services.nextcloud.ports,
        [`127.0.0.1:${state.ports.nextcloud}:80`])
    assert.equal(compose.services['wk-app'].image, state.weknora_image)
    const imageId = execFileSync('docker', ['image', 'inspect', state.weknora_ui_image,
        '--format', '{{.Id}}'], { encoding: 'utf8' }).trim()
    assert.equal(imageId, state.weknora_ui_image_id,
        'UI image tag moved after fixture preparation')
    for (const key of ['root_file_id', 'file_id']) {
        assert(Number.isInteger(runtime[key]) && runtime[key] > 0)
    }
    assert.match(runtime.knowledge_id, /^[0-9a-f-]{36}$/)
    assert.equal(typeof passwords.alice, 'string')
    return { scratch, state, runtime, passwords }
}

function privateEvidence(input) {
    const directory = path.resolve(input)
    fs.mkdirSync(directory, { recursive: true, mode: 0o700 })
    assert.equal(fs.statSync(directory).mode & 0o077, 0,
        'evidence directory must not be readable by other users')
    return directory
}

async function screenshot(page, directory, name) {
    const target = path.join(directory, name)
    await page.screenshot({ path: target })
    fs.chmodSync(target, 0o600)
}

async function closeWelcome(page) {
    // The first-run dialog is injected after the dashboard loads and its
    // wrapper class varies between Nextcloud builds. Use its visible actions.
    const skip = page.getByRole('button', { name: 'Skip', exact: true })
    await skip.waitFor({ state: 'visible', timeout: 5000 }).catch(() => {})
    if (await skip.isVisible().catch(() => false)) {
        await skip.click()
        const close = page.getByRole('button', { name: 'Close', exact: true })
        await close.waitFor({ state: 'visible', timeout: 5000 }).catch(() => {})
        if (await close.isVisible().catch(() => false)) await close.click()
        await skip.waitFor({ state: 'hidden', timeout: 10000 })
    }
}

async function closeGuide(page) {
    await page.locator('.guide').waitFor({ state: 'visible', timeout: 3500 }).catch(() => {})
    const close = page.locator('.guide__close')
    if (await close.isVisible().catch(() => false)) {
        await close.click()
        await page.locator('.guide').waitFor({ state: 'hidden', timeout: 10000 })
    }
}

async function browserFlow(fixture, evidence, playwright, executablePath) {
    const { state, runtime, passwords } = fixture
    const launch = { headless: true }
    if (executablePath) launch.executablePath = executablePath
    const browser = await playwright.chromium.launch(launch)
    let activePage
    try {
        const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
        const nc = await context.newPage()
        activePage = nc
        const ncBase = `http://127.0.0.1:${state.ports.nextcloud}`
        const uiBase = `http://127.0.0.1:${state.ports.weknora_ui}`
        await nc.goto(ncBase + '/login', { waitUntil: 'domcontentloaded' })
        await nc.locator('#user').fill('alice')
        await nc.locator('#password').fill(passwords.alice)
        await nc.getByRole('button', { name: 'Log in', exact: true }).click()
        await nc.waitForURL(url => url.origin === ncBase && !url.pathname.endsWith('/login'),
            { timeout: 60000, waitUntil: 'domcontentloaded' })
        await closeWelcome(nc)
        await nc.getByRole('button', { name: 'Open apps menu', exact: true }).click()
        await nc.getByRole('menuitem', { name: 'Files', exact: true }).click()
        const folder = nc.locator(`tr[data-cy-files-list-row-fileid="${runtime.root_file_id}"]`)
        await folder.waitFor({ timeout: 20000 })
        await folder.getByRole('button', { name: /Open folder/ }).click()
        const file = nc.locator(`tr[data-cy-files-list-row-fileid="${runtime.file_id}"]`)
        await file.waitFor({ timeout: 20000 })
        await file.getByRole('button', { name: 'Actions' }).click()
        await nc.getByRole('menuitem', { name: 'Details' }).click()
        await nc.locator('[role="tab"]').filter({ hasText: 'WeKnora' }).click()
        const link = nc.locator('.weknora-sidebar__link')
        await link.waitFor({ timeout: 20000 })
        assert.equal(await link.innerText(), '在知识库中提问此文件')
        const askUrl = new URL(await link.getAttribute('href'))
        assert.equal(askUrl.origin, uiBase)
        assert.equal(askUrl.pathname, '/platform/nextcloud-ask')
        assert.equal(askUrl.searchParams.get('file_id'), String(runtime.file_id))
        assert.equal(askUrl.searchParams.get('binding_id'), runtime.binding_id)
        await screenshot(nc, evidence, '01-files-sidebar.png')

        const popup = context.waitForEvent('page', { timeout: 10000 })
        await link.click()
        const wk = await popup
        activePage = wk
        await wk.waitForURL(/\/login\?next=/, { timeout: 15000 })
        await wk.getByText('Corporate directory', { exact: true }).click()
        await wk.locator('input[autocomplete="username"]').fill('alice')
        await wk.locator('input[autocomplete="current-password"]').fill(passwords.alice)
        await wk.getByRole('button', { name: '登录', exact: true }).click()
        await wk.locator('#nextcloud-ask-question').waitFor({ timeout: 30000 })
        assert.match(await wk.locator('.nextcloud-ask__source').innerText(), /acl-note\.txt/)
        await screenshot(wk, evidence, '02-file-question.png')
        await closeGuide(wk)

        let questionPayload = null
        let questionStatus = null
        wk.on('request', request => {
            if (request.method() === 'POST' &&
                request.url().includes('/api/v1/knowledge-chat/')) {
                questionPayload = request.postDataJSON()
            }
        })
        wk.on('response', response => {
            if (response.request().method() === 'POST' &&
                response.url().includes('/api/v1/knowledge-chat/')) {
                questionStatus = response.status()
            }
        })
        await wk.locator('#nextcloud-ask-question').fill(
            'Which synthetic approval code is in this document?')
        await wk.getByRole('button', { name: '提问此文件', exact: true }).click()
        await wk.waitForURL(/\/platform\/chat\//, { timeout: 20000 })
        await wk.getByText('ORCHID-QUARTZ-2749', { exact: true }).waitFor({ timeout: 30000 })
        await closeGuide(wk)
        assert.equal(questionStatus, 200)
        assert.equal(questionPayload.agent_enabled, false)
        assert.deepEqual(questionPayload.knowledge_ids, [runtime.knowledge_id])
        assert.equal(Object.hasOwn(questionPayload, 'agent_id'), false)
        assert.equal(Object.hasOwn(questionPayload, 'agent_source_tenant_id'), false)
        assert.equal((await wk.locator('body').innerText()).includes(
            'Current source access changed'), false)
        await wk.locator('.tree-root-expand').click()
        await wk.locator('.action-card.has-reference-trigger').click()
        const citation = wk.locator(`a[href*="/f/${runtime.file_id}"]`).first()
        await citation.waitFor({ timeout: 10000 })
        const href = await citation.getAttribute('href')
        assert.equal(href, `${ncBase}/f/${runtime.file_id}`)
        await screenshot(wk, evidence, '03-answer-and-original-citation.png')

        const opened = context.waitForEvent('page', { timeout: 10000 })
        await citation.click()
        const original = await opened
        await original.waitForLoadState('domcontentloaded')
        const originalUrl = new URL(original.url())
        assert.equal(originalUrl.origin, ncBase)
        assert(!originalUrl.pathname.endsWith('/login'), 'citation redirected to login')
        assert(originalUrl.pathname === `/f/${runtime.file_id}` ||
            originalUrl.pathname.endsWith(`/files/${runtime.file_id}`),
        'citation did not open the original file')
        // The Files route can serve its app shell even when file access is
        // denied. Read the exact synthetic file with this browser context's
        // Nextcloud session before claiming that the citation is usable.
        const dav = await original.context().request.get(
            ncBase + '/remote.php/dav/files/alice/Published/acl-note.txt',
            { headers: { Accept: 'text/plain' } })
        assert.equal(dav.status(), 200, 'cited original is not readable over authenticated DAV')
        assert((await dav.text()) ===
            'Which synthetic approval code is in this document? ' +
            'The synthetic approval code is ORCHID-QUARTZ-2749.\n',
        'authenticated DAV did not return the target file content')
        await screenshot(original, evidence, '04-original-file-open.png')
        return { status: 'PASS', project: state.project,
            ui_image_id: state.weknora_ui_image_id,
            file_id: runtime.file_id, knowledge_id: runtime.knowledge_id,
            question_http: questionStatus, agent_id_omitted: true,
            answer_marker: true, citation: href,
            opened_original_file: true, original_dav_http: 200, evidence }
    } catch (error) {
        if (activePage && !activePage.isClosed()) {
            await screenshot(activePage, evidence, 'failure.png').catch(() => {})
        }
        throw error
    } finally {
        await browser.close()
    }
}

async function main() {
    const args = argumentsForRun(process.argv.slice(2))
    const fixture = ownedFixture(args.scratch)
    const evidence = privateEvidence(args['evidence-dir'])
    const playwright = require(args['playwright-module'] || 'playwright')
    console.log(JSON.stringify(await browserFlow(
        fixture, evidence, playwright, args['browser-executable'])))
}

main().catch(error => {
    // Browser errors may contain page URLs, but never echo local credentials.
    console.error('Synthetic browser ask failed: ' + error.message)
    process.exitCode = 1
})
