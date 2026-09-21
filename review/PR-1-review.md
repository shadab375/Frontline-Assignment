# PR #1 review — LiveKit/Telnyx migration and warm transfer

Reviewed pinned base `5a98a66274322954a0a6255f54650eec6e50c41d` against
head `f93f7a5f79efdf08c18c583a6a6f7167084b93ae`.

## Merge recommendation: merge after fixes

The Daily-to-LiveKit boundary and the proposed Room1/Room2 transfer design are
coherent, but four verified failure-path defects can duplicate bot sessions,
misreport transfer state, or leave provider resources behind. Resolve the P1
items and add their regression coverage before merging. The P2 item should also
be addressed before production qualification because it amplifies webhook retry
behavior.

## P1 findings

### Concurrent webhook deliveries can start duplicate bot sessions

- **Location:** `voice-agent/server.py:112-129`.
- **Classification:** Verified defect.
- **Problem:** The handler checks `seen_livekit_events`, awaits `start_agent()`,
  then records the event ID. There is no reservation before the first await.
- **Failure scenario:** LiveKit delivers the same valid `participant_joined`
  event twice while the first request is blocked starting BotRunner. Both
  handlers pass the membership check and start a bot for the same room/caller.
- **Evidence:** The check is at line 112, `await start_agent()` is at 128, and
  insertion occurs at 129. `tests/test_server_livekit.py` only posts the duplicate
  after the first request completes, so it does not exercise this interleaving.
- **Impact:** Competing bots can publish/process the same carrier call and
  duplicate call-side effects.
- **Fix:** Atomically reserve the event before startup; roll back the reservation
  if startup fails. Use a bounded shared store if deployment spans workers/restarts.
- **Regression test:** Run two concurrent accepted webhook requests with a
  deliberately blocking `bot_starter`; assert one start and one duplicate result.
- **Confidence:** High.

### Broker departure can regress a terminal result to `CONSULTING`

- **Location:** `voice-agent/orchestrator.py:215-228, 288-293, 447-479` and
  `voice-agent/room2_pipeline_service.py:275-285`.
- **Classification:** Verified defect.
- **Problem:** The post-dial path has no terminal-state/attempt guard before it
  writes `CONSULTING`.
- **Failure scenario:** `dial_phone()` returns, then the broker disconnects before
  or during `mark_broker_answered()`. The Room2 callback terminalizes the attempt
  as `FAILED/broker_disconnected`; the original coroutine resumes and writes
  `CONSULTING` after the terminal transition has begun.
- **Evidence:** Terminal failure sets the phase before awaiting cleanup, whereas
  the dialing coroutine continues from its awaited calls directly to the
  `CONSULTING` transition. Existing disconnect coverage tests departure after a
  consultation has started, not this pre-consultation interleaving.
- **Impact:** Transfer audit/state can report an active consultation after Room2
  was torn down and Room1 restored; later callbacks or retries see contradictory
  state.
- **Fix:** Guard each post-await transition by attempt generation and terminal
  state, or centralize transitions behind compare-and-set semantics.
- **Regression test:** Interleave a broker-disconnect callback between dial return
  and `mark_broker_answered()`; assert the terminal reason/phase are preserved and
  no `CONSULTING` transition occurs.
- **Confidence:** High.

### Cleanup's second provider-operation attempt is unreachable

- **Location:** `voice-agent/orchestrator.py:501-517` (`_retry_cleanup`).
- **Classification:** Verified defect.
- **Problem:** The exception arm logs a failed attempt, awaits one loop turn, and
  returns `False` inside a `for attempt in (1, 2)` loop. Attempt two never runs.
- **Failure scenario:** A transient first LiveKit failure occurs while deleting
  Room2, removing the broker, or deleting Room1 on a terminal path.
- **Evidence:** The immediate `return False` follows the exception path inside
  the loop. The helper is used for all of the operations above. Existing transfer
  tests cover successful cleanup and primary-operation errors, but no cleanup
  operation that fails once then succeeds.
- **Impact:** A one-off provider failure can leave a room or participant behind,
  retain stale Room2 state, and make a later transfer fail cleanup.
- **Fix:** Move `return False` outside the loop; retain a bounded retry/backoff.
- **Regression test:** Make a fake cleanup operation raise once then succeed;
  assert it is called twice and the relevant room/participant state is cleared.
- **Confidence:** High.

## P2 findings

### BotRunner startup can hold webhook acknowledgement for the broad client timeout

- **Location:** `voice-agent/server_utils.py:33-52`, called by
  `voice-agent/server.py:128`.
- **Classification:** Verified defect.
- **Problem:** The webhook awaits `start_bot_local()` inline, but its
  `session.post()` has no request-specific timeout.
- **Failure scenario:** BotRunner accepts the TCP connection but never writes
  response headers. The webhook waits for aiohttp's broad client-session default
  rather than a short ingress deadline.
- **Evidence:** `create_app()` creates a default `aiohttp.ClientSession`; the
  `session.post()` call passes no `timeout=`. No existing test simulates a
  non-responding BotRunner.
- **Impact:** Webhook acknowledgement is delayed long enough to encourage
  provider retries, increasing exposure to the duplicate-start defect.
- **Fix:** Apply a short explicit `aiohttp.ClientTimeout`, return a retryable 503
  on expiry, and coordinate idempotency-reservation rollback.
- **Regression test:** Use a local BotRunner endpoint that accepts but never
  responds; assert bounded 503 behavior and no retained reservation.
- **Confidence:** High on the code path; medium on exact provider retry timing
  because no live call was placed.

## Plausible risks

None retained. Candidate concerns about outbound `202` timing and caller-ID/
tenant mismatch depend on an API and authorization contract not established by
this repository, so they are not presented as defects.

## Open questions

None blocking this review. The deployment model remains relevant to the
process-local idempotency design, but is recorded below as an assumption rather
than a new finding.

## Checks run

| Command | Result |
| --- | --- |
| `cd voice-agent && python -m pytest tests -v --tb=short` | Could not run: supplied workspace has no `voice-agent/` directory. |
| `cd shared && python -m pytest tests -v --tb=short` | Could not run: supplied workspace has no `shared/` directory. |
| `cd voice-agent && ruff check .` | Could not run: supplied workspace has no `voice-agent/` directory. |
| `cd web && npx tsc --noEmit` | Could not run: supplied workspace has no `web/` directory. |

The PR's public GitHub Actions page shows `functional-tests` succeeded, but the
unauthenticated log is unavailable here. Therefore the PR's stated test counts
and the claimed Ruff/TypeScript results are unverified, not failed.

## Assumptions and unresolved questions

- The documented one-active-bot-per-runner limit is intentional. Production must
  provide one runner per concurrent call, or replace global task ownership with
  room/call-keyed ownership.
- No credentials or live calls were used. LiveKit/Telnyx provisioning, speech
  services, recordings, Supabase storage, and end-to-end PSTN transfer behavior
  need development-environment qualification after the fixes.
- Process-local webhook deduplication does not survive restart or coordinate
  multiple workers. Whether that is acceptable depends on the deployment model.
