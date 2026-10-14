# Failed generation SSE completion

The actual pull-based SSE loop waited only for response_type=complete. A KnowledgeQA failure produced an error with done=true, so a first-request refusal could leave the HTTP reader waiting until socket timeout. The send loop now returns immediately on a terminal error, skips title waiting, and emits a static error without flushing retained source text or passing the backend's raw error payload. Continue polling follows the same terminal rule. Current source authorization checks remain before emission; revocation still emits its safe source-access terminal error.

The QA service-error event now contains a static diagnostic and generation_failed code. This change does not mark the assistant successfully completed, certify its retained body, or repair the separate completed-history selection problem.

Actual httptest.Server/HTTP-client tests prove the connection closes before one second, sends done=true/error, emits no success complete, and exposes neither backend sentinel text nor an intentionally held source tail. Revoked source, authorized/refused producer, old agent error type and poll-failure retained-tail gates passed11 tests/subtests with0fail/skip. The first compile failure and an old source-less staged-frame fixture failure remain recorded; the corrected frame fixture reaches the intended current-publication path. Full actual first-QA/history/model lifecycle verification remains required.

## Full application probe

`scripts/ops/synthetic-ldap-history-acceptance.py --scratch <owned-fixture>`
exercises two real QA requests in one personal session, two persisted completed
answers and both completed Continue replays. `--revoke-source-share` removes
only the fixture's source share and then checks saved history/replay redaction
while preserving the employee identity, KB grant and source owner's file.
The probe accepts only marker-owned direct/nested synthetic group-share stacks
and emits metadata without tokens or text. Syntax/import/help checks passed;
the combined candidate Docker application run is still pending. This harness
does not establish load performance or enterprise AD acceptance.
