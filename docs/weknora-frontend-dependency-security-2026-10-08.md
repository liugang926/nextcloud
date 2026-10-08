# Frontend dependency security validation

The complete pinned source patches now combine the interim history guards,
dormant message-lineage foundation, frontend dependency updates and a
persistent KaTeX security regression. The current complete patch hashes are in
[RAG adaptation](weknora-rag-adaptation.md). The
[sanitized evidence](evidence/weknora-frontend-dependency-security-2026-10-08.json)
records the pinned bases, exact patch/tree identities and verification scope.
Package identity remains `knowledage-base` version `0.8.0`.

The original frozen source had 13 npm production dependency findings (eight
high, three moderate, two low) and 14 full dependency findings, including one
development-only critical shell-quote finding. Both current combined source
profiles have **zero production and full dependency findings** in the checked
2026-10-08 registry snapshot. These are npm dependency-report nodes, not a count
of independent CVEs. Both pinned CI jobs now run the full npm audit without
lowering its failure level and include the persistent regression in their
focused frontend test list.

## Updated dependencies

| Dependency | Locked version |
| --- | --- |
| Vue and its matching compiler/runtime/server-renderer packages | `3.5.42` |
| Axios | `1.20.0` |
| DOMPurify | `3.4.16` |
| Mermaid | `11.16.1` |
| KaTeX | `0.18.2` |
| marked-katex-extension | `5.1.13` |
| browserslist / baseline-browser-mapping | `4.29.3` / `2.11.27` |
| fast-uri / nanoid | `3.1.8` / `3.3.20` |
| postcss / source-map-js | `8.5.29` / `1.2.2` |
| shell-quote | `1.12.0` |

The Vue advisory affects
[server-side attribute rendering](https://github.com/advisories/GHSA-g2v6-rqmx-r4w6).
The measured client bundles contain Axios, DOMPurify, Mermaid and KaTeX;
they contain no Vue SSR renderer, browserslist, baseline-browser-mapping,
fast-uri, nanoid, postcss, source-map-js or shell-quote modules. The latter
remain source/build-tool dependency concerns and were upgraded. Actual browser
fixture requests used XMLHttpRequest. Axios's Node HTTP/HTTP2 adapter is absent
from the measured client modules; its browser serialization/fetch paths remain
part of the dependency, so bundle inclusion alone does not establish remote
exploitability.

## Targeted KaTeX override and regression

The [KaTeX fix](https://github.com/advisories/GHSA-238p-pmpm-9mq7) begins at
`0.18.2`. Mermaid `11.16.1` declares KaTeX `^0.16.45`, so upgrading the direct
dependency alone would leave a vulnerable nested renderer. The targeted npm
override uses `$katex`, and the corresponding Yarn resolution is `^0.18.2`.
The direct renderer, marked-katex-extension and Mermaid all resolve the same
locked `0.18.2` package. Mermaid stays within major 11. Its declared KaTeX range
is deliberately overridden; the rendering fixtures below define the tested
compatibility scope.

`frontend/src/utils/markdownDependencySecurity.test.ts` rejects inherited
`Object.prototype.trust` when rendering an untrusted link and checks the three
consumers' package resolution. The same test failed as expected in an isolated
`npm ci` fixture with KaTeX `0.16.47`: raw rendering produced a `javascript:`
HTML anchor. Both tests pass on each patched profile. The browser fixture also
confirmed the existing shared sanitizer removed the unsafe link before
insertion; raw KaTeX now rejects the inherited trust earlier.

## Verification scope

Fresh worktrees extended the exact history/lineage heads with only the two
frontend package files and the persistent regression. Regenerated full patches
applied cleanly to their exact pinned bases. Their staged Git trees matched the
integrated source trees, and reversing each patch restored its original base.
The backend is byte-identical to the preceding history/lineage integration.

| Combined source profile | Full tests | Type check | Production build | Production / full npm audit |
| --- | --- | --- | --- | --- |
| Fixed c6 | 1,215 passed, 0 failed, 1 skipped | Passed | Passed | 0 / 0 |
| RAG77 | 1,219 passed, 0 failed, 1 skipped | Passed | Passed | 0 / 0 |

The existing browser handoff test remains opt-in through
`BROWSERSKILL_TEST_CHROMIUM` and `BROWSERSKILL_TEST_PLAYWRIGHT`; these variables
were unset. Local installs disabled install scripts, and full suites used two
test workers. The source frontend builds also use the CI/Docker
`VITE_IS_DOCKER=true` setting. No Docker image build is part of this validation.

Prior independent Chrome fixtures used synthetic local requests and formulas,
with zero chat-model calls. Five standalone formulas (powers, fraction/root,
integral, matrix and summation) and two Mermaid mathematical labels retained
equal MathML, labels and measured layout between KaTeX `0.16.47` and `0.18.2`.
The rendered 1,280 by 560 pixel comparison region had zero changed pixels out
of 716,800. The patched fixture also passed inherited-trust rejection, Axios
request/method protection and HTML sanitization checks. The complete frontend
Git trees are identical to those validated dependency source trees; this
composition did not rerun the browser fixture. These samples do not exhaust
all Mermaid diagram types or all application flows.

The frozen **7f images and schema 131 runtime do not contain this frontend
security update or the dormant schema 132 lineage foundation**. This is source
and CI integration; shared-runtime migration and complete V1 acceptance remain
separate work.
