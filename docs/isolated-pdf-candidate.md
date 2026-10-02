# Isolated PDF candidate acceptance

Build `weknora-ldap-app:nextcloud-rag-e5-anydoc-rebind` from the pinned RAG source and
patch, then run:

```sh
python3 scripts/ops/isolated-pdf-candidate-smoke.py
```

To exercise the short-text recovery branch in the same candidate binary, run
the controlled variant separately:

```sh
python3 scripts/ops/isolated-pdf-candidate-smoke.py --force-short-primary
```

This variant adds `DOCREADER_PDF_FORCE_SCANNED=1` only to the newly generated,
private fixture's DocReader service. It makes the primary result contain a page
image and little searchable text while the unchanged born-digital PDF still
has a complete local text layer. The run requires the application recovery log
with primary and recovered character counts meeting the branch thresholds,
then performs the same indexing, answer, citation, and revocation checks.
It does not alter a shared Compose file or image.

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
The `--force-short-primary` variant demonstrates the running candidate's
recovery branch, but uses a deliberately rasterized primary result. Neither
variant proves that an organically truncated PDF recovers, or that scanned-PDF
OCR succeeds on the candidate.

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

## Controlled short-primary run, 2026-09-30 UTC

The `--force-short-primary` driver exited 0 against the same candidate
app image `sha256:c8e8b10b275bc0abfe8446278132e35e4b87d79338d4f55c331cf4f84e42e551`
in owned loopback project `nc-synldap-5c3140eb`. The candidate logged 46
searchable characters from the primary DocReader result and 283 from AnyDoc,
with its short-text recovery marker. The PDF reached published/completed state
with two ready chunks and two embeddings; a ready chunk contained the protected
marker. Alice's file-scoped answer contained the synthetic approval code and
cited the original Nextcloud `/f/233` URL. Bob was denied. Removing only the
source group share denied Alice's old JWT access to the source, ask target,
direct content, search, and prior history citation on the first complete poll;
her LDAP group membership and the owner's PDF remained. The owned Compose
containers, volumes, and network were absent after cleanup.

This controlled run proves that the built candidate executes the AnyDoc
recovery branch in a complete Nextcloud-to-WeKnora flow. The primary short
result was induced by the fixture's DocReader setting, so a naturally truncated
born-digital PDF and scanned-PDF OCR remain separate acceptance cases.

## Combined browser and event-rebind candidate, 2026-09-30 UTC

The current candidate uses RAG patch SHA-256
`ed055900b1eca78cc15a14021794fb6dc95dafb3e8e5592ce3f865ca1538af03`
and app image
`sha256:d052febfcd39d3ea20e136a12a9dc10fda2389d14c118748764318f2d32f22dd`.
The normal driver exited 0 in owned project `nc-synldap-71cd7bce`:
two ready PDF chunks and embeddings, protected text, Alice's exact scoped
answer and original `/f/233` citation, Bob denied, and Alice's old JWT denied
after source-share removal. The controlled short-primary driver exited 0 in
owned project `nc-synldap-6de320ec`; primary searchable text was 46
characters and AnyDoc recovered 283. The same indexing, answer, citation,
old-JWT denial, and fixture-cleanup checks passed. These two runs do not
establish natural PDF truncation recovery or scanned-PDF OCR.
