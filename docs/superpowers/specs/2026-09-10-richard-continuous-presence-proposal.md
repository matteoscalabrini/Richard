# Richard: continuous presence, memory, and initiative

Status: proposed wider architecture, with the character and visual-behaviour change deployed on 2026-09-10 as Richard commit `0577aff`, the approved browser timing/visual-continuity milestone deployed on 2026-09-11 as `a169eb8`, and multilingual waiting phrase preparation deployed as `b8d9a54`. The vector database and embedding work are paused. The other architectural proposals remain available for subsequent work and have not been cancelled.

Date: 2026-09-10. Based on Richard `reachy-presence` at `eab7081`, the [implementation review](../../../REVIEW_PRESENCE_2026-09-10.md), and [PHILOSOPHY_REACHY.md](../../../PHILOSOPHY_REACHY.md).

## Current implementation scope: character first

The immediate change updates the existing default character prompt, the instruction used for unsolicited realtime turns, and the policy for using images. Richard is framed as a resident companion, with curiosity about people, shared projects, and surroundings; available memories and observations can prompt a question, an observation, or a return to a shared topic. Curiosity does not need a household task to justify it. Familiarity must remain grounded in actual history, and silence and the other person's attention remain valid choices.

Images are evidence for understanding, answering a specific visual question, investigating an uncertainty, or choosing a relevant action. Scene descriptions and inventories are appropriate only when requested. A request to look does not automatically request a description, and a spontaneous camera capture may end in silence. This operating policy applies to custom base prompts too. Richard's server and browser camera descriptions follow it; the shared system rule explicitly takes precedence over the stock Reachy client's suggestion to describe captures, preserving the unmodified client.

The default prompt retains the TARS-like style and personality dials. Custom base prompts retain their override behaviour. The existing action contract, limits on claims about perception, silence sentinel, tools, memory storage, wake triggers, and scheduling behaviour remain in place. A small realtime correction preserves the option to stay silent across client camera continuations; fresh user text, speech, or cancellation ends that unsolicited continuation. This step adds no background model calls or new persistent autonomy mechanism. Its prompt text is compact and static; it still requires model-based evaluation to establish the resulting conversational behaviour.

Only the vector database/embedding track is explicitly paused. Context budgets, episodic continuity, interests, and attention remain part of the wider proposal; they are outside this immediate character-only change.

### Behavioural checks for this step

- During an existing conversation, a request to look should lead to using the image in that context, without automatically listing the room's contents.
- An explicit request to describe a scene should receive a description; a specific visual question should receive a direct answer.
- An unsolicited observation may lead to a camera call followed by silence, a relevant question, or an observation connected to the shared situation.
- A fresh user turn during or after that camera call must take priority and must not inherit silent-response filtering.
- Familiarity and follow-up questions must use actual available history; repeated greetings, invented off-session activities, and obligatory offers to help are unwanted.

Automated tests cover prompt assembly and the realtime continuation protocol. These checks require a separate live model evaluation for conversational quality; unit tests cannot establish that the model consistently adopts the intended character.

Local validation on 2026-09-10: the six-file [implementation patch](../../../results/2026-09-10-character-change.patch) was reviewed and applied to Richard's `reachy-presence` worktree over `eab7081`. The applied worktree passed **769 tests**, with **2 skipped and 3 deselected**; `git diff --check` passed. The new regression tests cover silent and spoken continuations after one or more client camera calls, and fresh text, multipart text, speech, cancellation, or concurrent typed input taking priority. No deployment or live model evaluation was performed in this step.

Deployment followed at Matteo's explicit request: commit `0577aff` was pushed to the existing public `reachy-presence` branch and fast-forwarded on CT123. Richard restarted at **2026-09-10 10:45:13 UTC**. The deployed default persona and source path were verified, the realtime check returned `session.created`, and the web API returned HTTP 200 both locally and from the Mac. The deployed checkout is clean. No production inference prompts were sent; Matteo owns the live conversational evaluation. Open a new session to use the new prompt.

## Next browser milestone: conversational timing and visual continuity

Implemented and deployed on 2026-09-11 at `a169eb8`. Technical verification passed; Matteo owns listening and live conversational evaluation. See the [verification report](../reports/2026-09-10-browser-presence-verification.md).

The approved follow-up was deployed at `b8d9a54`: separate English/Italian and voice caches, preparation after relevant settings saves, detected spoken-language selection in auto mode, and **Configuration → Voice → Regenerate waiting phrases**. Preparation waits for voice sessions to close; the cache retains at most eight recent variants. Twelve clips are ready on Richard. See the [multilingual verification report](../reports/2026-09-11-multilingual-cues.md).

Matteo's feedback after the character deployment: the result is better, but realtime voice and vision still feel too much like a speaker with a shutter button. He is testing Richard in the browser; the physical Reachy has not arrived. Both conversational pauses/interruptions and the need to direct each visual observation matter equally. He explicitly suggested preset phrases during thinking and tool-processing gaps, such as "let me think" and "let me see". These are requirements for the next design, not features deployed with `0577aff`.

### Brief speech during a real wait

Use a small library of short clips prepared in Richard's configured voice and language. Reuse the existing TTS and voice-effect settings when preparing them, then cache the audio outside the model context. Cache identity must include the voice, language, and effect configuration; stale clips from another voice must not play. A missing clip means silence until it is prepared outside the active conversational path.

This is preferable to asking the main model to generate filler: the main model is the operation being waited for. Generating a new TTS request during each gap also adds latency and can compete with the answer. Prepared clips can start without an additional model or synthesis request during the turn. The exact spoken variants still need listening checks, especially non-lexical sounds such as "mmm".

Tie playback to explicit response and tool lifecycle events, including tool execution inside the server. A timer alone cannot establish that a picture has been taken. Generic waiting clips can accompany a pending solicited response. A visual clip such as "Let me see…" becomes eligible only once an image is available for processing. Do not announce tool success or describe a scene through a preset clip; preserve the existing action-result contract.

Proposed initial policy, subject to listening evaluation:

- Wait approximately 1.2 seconds of audible silence before a clip becomes eligible. Fast answers produce no clip.
- Play at most one clip per waiting interval and at most two per logical user turn, including camera continuations. Separate clips by at least eight seconds and avoid repeating the last variant.
- New user speech, a cancelled response, a camera failure, or connection closure invalidates pending clips. A ready answer has priority over a clip, with a short fade if necessary rather than waiting for the clip to finish.
- Richard must know whether the browser is still playing response audio; sending the last audio chunk is not the same as finishing speech. Clips must never overlap that audio or run after the turn has ended.
- Unsolicited observation remains silent by default. Waiting clips must not make an otherwise silent camera investigation audible.
- Clips are delivery cues, not model-written answers. Keep their text out of conversation history and memory; retain only the bounded runtime state needed for timing and repetition control.

Acceptance checks cover a fast answer, a slow answer, server and client camera paths, repeated tool continuations, interruption during playback, a late result from a cancelled turn, failure/disconnection, voice-cache invalidation, and an unsolicited turn choosing silence. Measure actual browser playback timing separately from server response completion. Listening evaluation must establish that clips sound natural and do not postpone the answer.

### Visual continuity remains part of the same goal

Waiting clips address the audible gaps. The browser still needs its voice camera and ambient frame source coordinated with the same conversation. Fresh, relevant observations should be eligible to inform a spoken turn and significant scene changes should be reconsidered when attention becomes available. Source identity, capture time, stale-image handling, and a bounded selection of current observations are part of that work. There must be no automatic inventory of every scene or accumulation of every frame in the prompt. The vector database track remains paused and is not a prerequisite.

## Intended experience

Richard participates in the home through a continuing history, attention to people and surroundings, and interests he can pursue across encounters. Device control is one of the ways he participates. An interest may concern a person, a shared project, or something in the environment without an immediate practical purpose.

The first release should demonstrate both of these sequences:

- A conversation about an unfinished project leaves Richard with an open question. He retains why it matters, survives a disconnected session, and later chooses a suitable opportunity to return to it. A response or correction changes what he does next.
- A scene change gives Richard an opportunity to look. He investigates something unfamiliar, records uncertainty, and can choose another observation later without asking Matteo to explain everything. This can happen without an open voice session.

These are evaluation situations, not scripted behaviours or mandatory lines. Deciding to wait, revise an interest, or abandon it is valid.

## Approaches considered

| Approach | Benefit | Limitation |
|---|---|---|
| **Recommended: persistent presence around the existing engine** | Connects dialogue, perception, memory, and future attention; can be introduced incrementally. | Requires explicit state, scheduling, and session coordination. |
| Expand the personality prompt and wake on more events | Small initial change; useful later as part of the full design. | Leaves fragmented histories and no reliable lifecycle for interests. More invocations can produce repetitive remarks. |
| Keep one indefinitely growing conversation and invoke it frequently | Makes recent continuity straightforward initially. | Context growth, reconnection, forgetting, contention, and retrieval remain unresolved. The philosophy did not select an endless conversation. |

The recommendation preserves the current local model, tools, perception pipeline, and stock Reachy application. Model replacement is not a prerequisite. Model capability and latency will be measured after the missing mechanisms exist.

## 1. A persistent owner for attention and history

Add a process-level `PresenceRuntime` to Richard's server. It owns pending opportunities and coordinates access to shared memory and interests. Realtime connections become input/output channels into this presence, retaining the local state required for audio and turn handling.

Responsibilities:

- Accept conversation events, admitted perception events, completed tool results, and due interests.
- Record stable event identities and preserve selected evidence in shared history.
- Select the next eligible opportunity, give direct human interaction priority, and suspend background work at safe boundaries.
- Reconsider deferred work when a conversation ends or a relevant new event arrives.
- Route an utterance to one appropriate live channel and record its delivery outcome.

A disconnected client removes an output route. It does not erase Richard's interests. Autonomous observation can continue while a camera source is live. With no camera source, Richard knows he cannot currently look; a camera dropout does not imply somebody left.

Background opportunities have lifecycle states such as pending, running, deferred, completed, and expired. Duplicate event delivery must not run the same opportunity twice. A restart rechecks unfinished opportunities against current conditions rather than replaying an old action blindly.

## 2. One model, three kinds of invocation

All invocations use the same identity and shared history. Their immediate purpose and available actions differ. These are modes of the existing model, not separate characters.

| Mode | Trigger | What the model can do | Output |
|---|---|---|---|
| Dialogue | A person speaks or writes | Converse, retrieve, remember, use allowed tools, create or revise an interest. | Stream a response through the current channel. |
| Attention | A salient event, a due interest, or an eligible quiet opportunity | Inspect available evidence, use the camera, retrieve episodes, update an interest, decide whether an interaction is warranted. | A bounded decision; speech only when explicitly selected. |
| Reflection | A meaningful episode closes, or significant new evidence accumulates | Preserve useful episodes, revise beliefs and interests, connect a correction to an earlier assumption. | Memory changes and future opportunities. |

Dialogue continues to use the direct streaming path. It does not wait for an additional planning-model call on every utterance. The initial implementation uses the configured conversational brain for all three modes; tuning or splitting model roles is a later measured choice.

Background text is not automatically sent to TTS. The attention path returns a small validated decision: what to do, which interest or evidence it relates to, whether to defer, and an optional intended utterance. Logs retain these decisions and their evidence references, not hidden reasoning. Missing or invalid decisions become recorded failures or deferrals, never accidental speech.

Background work has configurable limits on model turns, tool rounds, and elapsed time. Direct speech takes priority. Since the current backend's cancellation is cooperative, preemption initially happens at reliable request/tool boundaries; immediate interruption of inference is not promised. A tool already changing the world must have its result reconciled before retrying.

## 3. Memory of encounters, with room for change

Keep SQLite and evolve the existing memory facilities around three related records:

| Record | Meaning | Minimum useful information |
|---|---|---|
| Episode | A meaningful encounter or action and its outcome. | Stable ID, time, source, participants where known, selected original context, observations/actions/results. |
| Belief | What Richard currently thinks is true. | Statement, stated/observed/inferred origin, supporting episodes, uncertainty, temporal scope, correction/supersession links. |
| Interest | Something Richard wishes to understand, follow, or honour. | Question or purpose, origin episode, current understanding, next possible step, relevant opportunity, status, previous attempts. |

Preserve raw context for selected meaningful episodes so later retrieval can recover why a conclusion changed. Keep transient working context bounded; this proposal does not require indiscriminate permanent recording of every frame or utterance. Repeated delivery of one event retains one evidence identity, rather than increasing confidence through duplication.

The existing `remember` and `forget` tools remain available. Add retrieval and the ability to revise records; extend memory writes with provenance. Import existing facts with their IDs and creation times, marking their origin as legacy/unspecified rather than inventing evidence.

In the proposed memory architecture, each model invocation receives the recent relevant episode, current situation, matching beliefs, and a small selection of eligible interests. The complete memory database would no longer live inside a permanently pinned system prompt. Identity and operating instructions remain cacheable; changing context is supplied separately. Explicit context budgets and structured/lexical retrieval belong to the future continuous-interest milestone. The semantic/vector extension described below is paused and is not a prerequisite for that milestone. The search backend remains replaceable.

Forgetting removes the targeted material from durable records, dependent summaries/interests, and active contexts. It also invalidates in-flight responses that could still expose it. This deliberately takes precedence over prefix-cache reuse. Independent evidence should survive where it does not depend on the erased material. Corrections supersede earlier beliefs and change subsequent retrieval.

### Bounded context and hybrid memory retrieval

The vector/embedding portions of this section are retained as a paused design option. They do not belong to the current character change. The bounded-context requirement remains part of the broader proposal.

Matteo raised accumulation and response latency as a central design concern. The working context must have a configurable ceiling independent of the number of stored memories. A vector database can help select memories; selecting vectors alone does not impose that ceiling or prevent conversation history from growing.

Use three retrieval paths together:

- Structured lookup for people, sources, time ranges, active commitments, corrections, and interests whose next opportunity is due. These must not depend on semantic similarity to the latest utterance.
- Lexical search for names, exact phrases, identifiers, and specific project terms.
- Semantic search over local embeddings for related meanings, paraphrases, and connections across episodes. Validate retrieval in Italian and English, including cross-language queries.

Combine a bounded candidate set, remove duplicates and obsolete records, and select short evidence-linked passages within a token budget. Similarity is a relevance signal, not evidence that a memory is true. Keep uncertainty, dates, and current correction state with retrieved text. An empty or weak result is valid; do not fill the budget with unrelated memories. Richard can request a specific original episode through `recall` when details matter.

SQLite remains the authoritative store for text, provenance, versions, and relationships. Lexical/vector indexes are derived and rebuildable. SQLite's [FTS5](https://www.sqlite.org/fts5.html) supports lexical search and BM25 ranking. An embedded vector extension such as [sqlite-vec](https://alexgarcia.xyz/sqlite-vec/) can supply vector search without an additional service. Treat it as a candidate pending package/runtime compatibility, release selection, and corpus-scale measurements. A local [Qdrant backend](https://qdrant.tech/documentation/search/hybrid-queries/) is an alternative with hybrid-query support if measurements justify a separate engine. The backend and embedding model are proposed choices to benchmark, not installed dependencies.

For the ordinary voice path, start evaluation with a **1,200-token ceiling for retrieved memory**, configurable and explicitly provisional. This covers memory excerpts and their provenance; identity, tool schemas, current input, and recent dialogue have separate allocations within an overall input ceiling. Do not automatically expand the memory allowance as the archive grows. A deliberate deeper recall uses a separately bounded allowance and may take longer.

Bound recent conversation, old tool results, retrieved blocks, and images as well. Retain complete pending tool exchanges and the current request. At a window rollover, retain a compact working account linked to selected original episodes; retrieve those originals when needed rather than repeatedly summarising summaries. Repeated retrieval of the same record/version should not add another copy to history.

Compute embeddings for new or revised memories asynchronously using a small local embedding model. Retrieval still needs a query embedding; include that cost in the latency budget and reuse it only for matching query/model versions. Newly committed memories are immediately available through structured and lexical retrieval while indexing catches up. Before serving any vector candidate, validate its ID and version against the authoritative record so delayed indexing cannot resurrect a deleted or superseded memory. Persist deletion of derived index entries and evict dependent caches.

Preserve the stable identity/tool prefix and avoid gratuitous context rewrites. Refresh selected memory when topic, evidence, or record versions change; reuse valid selections otherwise. Changes, forgetting, and rolling the conversation window can invalidate part of the prompt cache. Measure that cost explicitly: bounded history and indefinitely perfect cache reuse cannot both be promised. Initial retrieval should not require an extra generative-model call to rewrite the query or rerank results.

Embedding/index failure falls back to bounded structured/lexical retrieval within a deadline, without claiming that no relevant memory exists. Background indexing yields resources to speech. Archive size can still affect search latency even with a fixed prompt budget, so storage and model costs must be measured separately.

## 4. Interests can create their own future opportunities

Introduce tools conceptually equivalent to `follow_interest`, `revise_interest`, and `close_interest`. Richard may use them without asking Matteo to create a reminder. Their purpose includes curiosity, learning about people, and shared commitments.

An interest may become eligible at a time, after a relevant event, when a particular person is available, or during a later quiet opportunity. Eligibility means reconsidering the interest; it does not mandate speech or an action.

The scheduler can reuse the existing schedule parsing and due-time machinery. It should use a separate interest lifecycle from user-requested control loops. Existing monitoring instructions continue to govern household automations; they must not also prohibit Richard from scheduling his own attention.

Avoid unlimited accumulation. Richard can merge related questions, resolve them, lose interest, or defer them with a meaningful condition. Lack of an answer is not a reason to ask the same question repeatedly. A declined topic or a request for space changes future eligibility.

## 5. Opportunities to think and opportunities to speak

Retain the cheap perception pipeline and deterministic repetition filter. Scene changes and other admitted transitions can create opportunities for attention as well as arrivals. The sensor is not assumed to understand objects: the model can request a picture to learn what changed.

Add due-interest checks and a sparse, configurable opportunity to consider existing interests during quiet periods. A scheduler check with nothing eligible does not invoke the model. The model may also choose to look around after a sufficiently old observation, within the background budget. This permits exploration without requiring a person to supply a question.

Before delivering unsolicited speech, revalidate:

- Is the intended person or audience plausibly available, with fresh enough evidence?
- Is a conversation or user speech already underway?
- Has this topic recently been raised, declined, or resolved?
- Does the utterance still fit the current situation and delivery window?

Deterministic limits handle repetition, quiet periods, and resource use. The model judges relevance and chooses whether to engage. Useful outcomes include looking, remembering, waiting, and abandoning an inquiry. The number of spoken interventions is not the optimisation target.

A short-lived delivery record identifies the selected channel, intended content, expiry, and spoken/cancelled status. Expired greetings are discarded; a still-relevant underlying interest can survive. This prevents an old line from being spoken after a long disconnect and prevents multiple sessions from independently announcing it.

## 6. Character and capabilities

Rewrite the base prompt to establish Richard's relationship to the home: familiarity through history, interest in people and surroundings, freedom to investigate, candour about uncertainty, and consideration for others' attention. Preserve the humour and directness settings as style choices.

Character becomes observable through what Richard chooses to remember and return to. Initial dispositions can guide that process; invented preferences or claims of subjective experience are not required. Learned changes should be traceable to encounters and corrections.

Autonomous attention includes remembering, recalling, looking through available cameras, and choosing future investigations. Household control and physical movements use an explicit capability policy. The philosophy's exploratory freedom does not silently expand permissions for devices. Ordinary dialogue and already-authorised household requests retain their existing route.

For embodiment, complete the approved realtime transport and body work. Expressive gestures can use the planned markers. Investigative head movements need an acknowledgement and a fresh post-movement observation linked to the interest; a dispatched command alone does not establish that the viewpoint changed. When the body is unavailable, that investigation is deferred or uses another available source.

## 7. Implementation boundaries and delivery order

This proposal spans several subsystems. Each phase should have its own bounded implementation plan and reviewable completion criteria.

| Phase | Main change | Completion evidence |
|---|---|---|
| **A. Reliable context** | Fix stale presence and active-context forgetting; introduce evidence IDs and separate stable instructions from changing memory. | Camera loss reports uncertainty; deletion removes material from subsequent active-session requests; repeated events do not become new evidence. |
| **B. A first continuous interest** | Add shared episodes/interests, bounded structured/lexical retrieval, a minimal runtime and scheduler, background decisions, and one-channel delivery. Integrate the character changes with this flow. Vector retrieval remains optional and paused. | Both example sequences work across reconnection; deferral survives the current turn; corrections affect later decisions; growing the archive does not expand the configured memory/context budgets. |
| **C. Richer shared history** | Extend provenance, retrieval, reflection, interest revision, and continuity across channels. | Richard can recover the reason behind a past decision; meaningful retained episodes affect later behaviour without flooding the prompt. |
| **D. Embodied investigation** | Complete the compatible realtime/body path and connect intentional movements to observations. | A chosen viewpoint produces acknowledged movement, a fresh image, and a recorded change in understanding. |

Phase B includes the minimum episode storage and retrieval needed for continuity. Those essentials are not postponed to phase C. It should be demonstrable through the browser camera and voice channel before hardware integration becomes its critical dependency.

Code boundaries: `cli.py` assembles the runtime; `realtime/session.py` and its registry become channel adapters; `engine.py` supports distinct streaming dialogue and silent background execution; memory/provider code serves evolving shared context; perception supplies source-tagged observations; the existing control-loop subsystem remains responsible for configured automations. New presence/interest modules own their own state rather than growing these existing files into a single coordinator.

The approved transport specification remains the basis for protocol compatibility. Its old image-placeholder proposal has already been superseded by implemented multimodal messages; transport work must preserve the current camera and image paths.

## 8. Evaluation and operational visibility

Use deterministic tests with fake clocks, synthetic events, and fake model decisions for scheduling, deduplication, delivery selection, context invalidation, and reconnect behaviour. Replay a situation across multiple sessions, not just isolated prompt/response pairs.

Then use authorised local-model sessions to evaluate whether the offered opportunities actually lead to coherent choices. Framework tests prove the mechanisms; they cannot prove that Richard develops worthwhile interests. Preserve comparable situations and model settings when measuring a change.

Record enough to answer: what prompted this activity, what episode or interest supported it, what Richard chose, what happened, and what changed later. Measure repeated unsolicited topics, expired/deferred opportunities, successful continuations, correction use, background inference cost, and interference with voice latency. An observation can be successful without producing speech.

For memory retrieval, benchmark the same relevant situations with synthetic archives of 1,000, 10,000, and 100,000 records. Compare structured/lexical retrieval against hybrid retrieval for paraphrases, exact names, older relevant episodes, corrected facts, and due interests. Record query-embedding time, search time, retrieved tokens, total prompt tokens including images, cold/warm prefill behaviour, and time from end of speech to first audio (p50/p95). Verify the token ceilings mechanically. Compare proposed backends on the actual host before claiming latency savings; no such measurements have been performed for this proposal.

On model failure, retain the unresolved interest with bounded retry/backoff. On stale perception, mark current knowledge uncertain. On session loss, cancel stale delivery while preserving worthwhile attention. On restart, reconcile unfinished actions and do not blindly repeat side effects.

## Proposed first milestone

After the authorised character-only change, phases A and B remain the proposed first architectural milestone: **Richard can originate or inherit one meaningful interest, pursue it quietly when appropriate, retain its evidence through a pause, and make a different later choice because of what happened.**

This milestone makes the philosophy testable while preserving the existing domestic usefulness. It provides the basis for richer memory and embodied exploration without requiring a full rewrite or a new model first.
