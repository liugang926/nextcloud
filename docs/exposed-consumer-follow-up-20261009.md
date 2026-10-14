# Exposed consumer follow-up

This is a source audit of RAG candidate `595f241f`, not a runtime exploit report
or acceptance result. It keeps the remaining V1 scope explicit after the
parent-copy and title component checks. Paths below belong to the pinned
WeKnora component source. No application, Go test or production setting was
changed for this audit.

1. Background memory extraction is registered and QA finalization schedules it
   when workspace memory is enabled with automatic writing. `memory/extract.go`
   reads paginated session messages at line 465, prior context at line 532,
   then calls the model at line 988. Its queue input has no immutable message
   proof or original actor binding, and it does not hold content/body leases.
   The current `messages` bodies are inline: the body152 availability check
   checks live/purge state and the journal, but does not verify selected bytes
   against their original admission or require a material read plan. The next
   actual case must enqueue through the real producer, change current bytes
   without a new admission, then require zero model calls, no memory write and
   no cursor advance. Actor/source withdrawal and lifetime cases follow.
   `/memory/items` and `/memory/export` also need the resulting source contract.
   Normal RAG and Agent memory recall already reject unsupported original
   material; that functional denial is not evidence of successful disclosure.

   A source-only actual-task fixture is now frozen at `27302181`: it uses
   real message creation/completion, `ScheduleExtraction`, the SQL pending
   queue and `Handle`, with model and enqueue transport spies. Its six cases
   plus two parents cover PostgreSQL/SQLite legal input, changed current bytes
   without a new admission, and inactive original actor. API signatures were
   independently checked, but compilation and actual baseline/red execution
   remain pending. This is not Redis broker or restore evidence.
   The production correction must also cover prior context and relevant
   existing memory entries in the prompt. Current prior-context failures are
   swallowed, and derived memory rows have no original-material receipt.
   Pending work can merge sessions, so a trigger message ID alone cannot prove
   the exact admitted page, cursor, actor, model and configuration. Hold the
   original inputs through generation and the item/cursor commit; unknown
   historical entries cannot be newly signed to make them eligible.

2. Skill installation still installs the old `installSteerSink` in
   `tenant_skill_install.go:1038`. The new Agent requires `OriginalSteerSink`,
   so this is a functional refusal before model consumption. The independent
   transcript route in `sandbox_skill.go:793` reads Redis events and emits
   their Content/Data at line 848 without original material or read leases;
   original message creation failure at line 891 does not stop subscription.
   Installation input and guidance need an immutable run/actor/body contract,
   followed by a guarded transcript. Actual cases must replace cached events
   and revoke Admin after the first frame, with no further body or fake
   completion. The source audit has not executed those cases.

3. IM callbacks and MCP endpoints are exposed, but their actual principals are
   unsupported by `captureKnowledgeActorSelector` in
   `knowledge_build_actor.go:95`. Message creation consequently refuses a
   legitimate non-Web request. Restore functional support with original
   channel/endpoint, actor and input bindings, then keep the material holder
   through the final adapter send or MCP return. Test channel disable, token
   rotation and send-time withdrawal. This is not a successful leakage claim;
   MCP's explicit denial of Nextcloud personal sources must remain.

FAQ import/export already use original material and HTTP read holders. Artifact
reads refuse missing producer contracts. Cross-tenant Agent paths have current
sharing and execution checks; this audit did not confirm a new bypass in them.
These findings do not change the normal application, restore, inventory,
physical-GC, performance or enterprise acceptance gates.
