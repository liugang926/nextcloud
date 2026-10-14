# Held stream output after event-store failure

The actual `ContinueStream` polling error branch previously flushed public URL
holdback content directly. A source withdrawn during a failed event-store read
could therefore release a previously authorized, buffered answer fragment.
The store error branch did not run the later live-publication checks.

Before flushing, it now rechecks the whole saved/live message and its already
accumulated source references. Denial or unavailable authorization discards the
tail. A cancelled request also returns without output. A still-authorized
request retains the existing tail behavior. The URL renderer stays unchanged;
its transformation does not confer source authority.

## Reproduction and verification

On both current pinned c6/RAG77 complete trees, the same real-handler fixture
held synthetic text inside an incomplete Markdown resource reference. It
asserted an active client, offset 1, exactly two event reads, an authorized
prefix already sent, no held marker yet sent, and the persisted Nextcloud
reference reaching the live verifier. The following cases were red before the
fix: withdrawal, unavailable authorization and cancellation on poll error.
Authorized poll error and withdrawal on successful poll were green controls.

All five cases passed with the correction. Each complete session package then
passed 370 test/subtest results, zero failures and zero skips; `go vet` passed.
Both full patches cleanly apply to their pinned bases, reconstruct their exact
frozen trees, and reverse to their exact bases. Only the stream branch and its
persistent regression differ from the preceding score/denial correction.
See the [source and evidence record](
evidence/nextcloud-stream-poll-failure-authorization-2026-10-08.json).

These are networkless, local-stub source checks. They do not deploy a new
backend image, establish live event or enterprise acceptance, activate the
separately staged typed producers/consumers, or authorize physical derived GC.
The shared backend remains RAG77 + 7f/schema131.
