# Isolated PDF candidate acceptance

Build `weknora-ldap-app:nextcloud-rag-e5-anydoc` from the pinned RAG source and
patch, then run:

```sh
python3 scripts/ops/isolated-pdf-candidate-smoke.py
```

The smoke checks the candidate image's source and patch labels before it
creates anything. It starts a fresh, uniquely named synthetic LDAP Compose
project on random loopback ports, bootstraps an actual Nextcloud source pair,
and runs the two-account text-file permission matrix. It then uploads a small
born-digital PDF to the paired folder and requires:

- `GET /api/v1/system/parser-engines` to report a connected DocReader and
  `anydoc` with `Available: true` in the candidate binary;
- the PDF to reach a published source version with a completed parse, a ready
  chunk, an embedding, and its protected text in a ready chunk;
- Alice's source and file-scoped question to return the expected answer and a
  citation to the original Nextcloud `/f/<file_id>` route, while Bob is denied;
- after deleting only the owned Nextcloud group share, Alice's **old JWT** to
  lose DAV, signed source, ask-target, knowledge, chunks/preview, document
  search, and prior answer/citation history access. Her LDAP membership, the
  WeKnora Engineering KB grant, and the owner's PDF stay intact.

The script reports `short_text_recovery_observed` separately. A normal PDF
that DocReader fully extracts never enters the short-text AnyDoc recovery
branch; in that case the flag is `false` and the run establishes binary
availability and PDF ingestion, not recovery of a truncated DocReader result.
The focused Go test for that branch uses a controlled short primary result.
Neither this smoke nor that unit test proves scanned-PDF OCR on the candidate.

The fixture stores generated credentials and Compose files in a private 0700
scratch directory. The smoke removes only its own marker-verified Compose
project, volumes, and scratch directory in `finally`, including after a test
failure. It never switches the LAN development stack or uses its credentials.
Output includes no passwords, tokens, PDF text, SSE body, or HTTP error body.
If cleanup itself fails, the script prints the scratch path so an operator can
run `synthetic-ldap-fixture.py destroy --scratch PATH` after inspection.

## Observed candidate run, 2026-09-30 UTC

At `04:50:27Z`, the full driver exited 0 against app image
`sha256:c8e8b10b275bc0abfe8446278132e35e4b87d79338d4f55c331cf4f84e42e551`
in owned project `nc-synldap-cc375364`. The initial text-file matrix passed:
Alice could read the Nextcloud DAV and signed source, WeKnora knowledge and
search; Bob could log in but had none of those content reads. The paired PDF
was file ID 233, reached published/completed state, and had two ready chunks
and two embeddings. A ready chunk contained its protected marker. Alice's
file-scoped answer contained the PDF's synthetic approval code and cited the
original Nextcloud `/f/233` URL; Bob's ask target and document search were
denied. After the owned group share was removed, Alice's pre-existing JWT
lost the PDF source, ask target, direct content, search, and prior history
citation on the first complete poll. Her Engineering membership and the
owner's PDF remained.

`anydoc` reported available and DocReader connected. The generated PDF did
not trigger the short-text recovery branch
(`short_text_recovery_observed=false`), so this run does **not** establish
runtime PDF text recovery when DocReader truncates its result. The marker-
verified Compose containers and volumes were absent after the driver exited;
the private scratch directory was removed by the driver.
