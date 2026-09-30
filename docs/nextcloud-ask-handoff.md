# V1 Files-sidebar question handoff

The Nextcloud Files tab offers a file-scoped question link only after its own
session has read access to the source file and publication root, publication is
active, and the signed WeKnora file-status response reports `ready` for the
same current ETag. A second Nextcloud source read follows the machine status
request. The existing `weknora_web_url` app setting is the browser origin; it
must be HTTPS (or loopback HTTP for local development) and use `/` or `/login`
as its path. No new credential or configuration item is needed.

The navigation URL is:

```text
<WeKnora origin>/platform/nextcloud-ask?instance_id=<id>&binding_id=<id>&file_id=<decimal>&source_etag=<etag>
```

These values are untrusted source selectors, not identity assertions or access
tokens. The browser receives no tenant ID, knowledge-base ID, machine secret,
directory GUID, or answer context. `qa_available` stays `false` in the signed
machine status because it does not prove personal retrieval permission.

WeKnora preserves this local route through its password/LDAP and OIDC login.
After login its `GET /api/v1/integrations/nextcloud/ask-target` accepts exactly
the four selectors above. It requires an interactive web principal, finds one
active paired source and its `published` current candidate, checks the
authenticated user's KB and group read access, and performs a fresh Nextcloud
source authorization including ETag equality. It rereads the published target
after that network check. Missing, stale, malformed or withdrawn candidates
fail closed; source outages return an unavailable response. API keys cannot use
the route. The response contains only the exact knowledge ID, KB ID, title and
ETag after authorization. It has `Cache-Control: no-store`.

The WeKnora page redoes the target check when the employee submits a question.
It resets previously selected KBs, files, tags, tools, agents and web search,
then opens a new personal quick-answer session with exactly that document ID.
The regular WeKnora retrieval path checks the user's current KB grant and
Nextcloud publication before source content reaches the model or the answer.
The employee may later adjust a normal chat's scope in WeKnora; each resulting
request continues to use WeKnora's authorization checks. The handoff does not
provide Nextcloud SSO, an embedded chat, or a delegated token. Those are V1.1
work. A logged-in WeKnora account must be linked to exactly one fresh,
verified AD identity on both systems; the app never substitutes its machine
connection for that user.

Acceptance still needs a live two-user fixture with one published, indexed
document: the mapped permitted user follows the Files link, authenticates to
WeKnora, asks a question and sees a citation that returns to the permitted
Nextcloud original. A denied or revoked user must receive no target and no
source text through direct API, chat history or alternate retrieval routes.
Unit and contract tests alone do not establish that end-to-end result.
