# PR #1 review: Daily to LiveKit/Telnyx migration

**Pinned comparison:** `5a98a66274322954a0a6255f54650eec6e50c41d` →
`f93f7a5f79efdf08c18c583a6a6f7167084b93ae`.

## Recommendation: request changes / do not merge

The migration has a sensible boundary shape: webhook parsing, LiveKit room/SIP
operations, transport creation, and the Room1/Room2 transfer state machine are
separated, and the PR explicitly distinguishes provider-dependent qualification
from mocked checks. However, webhook idempotency has a race, a broker-disconnect
callback can regress a terminal transfer result, and BotRunner startup can delay
webhook acknowledgment for the client's broad default timeout.
These are material call-setup/transfer failures, rather than release-
qualification risks.

## Required review comments

### P1 — webhook retries can start duplicate bot sessions

**[`voice-agent/server.py:112-129`](https://github.com/e3-solutions/Frontline-Assignment/blob/f93f7a5f79efdf08c18c583a6a6f7167084b93ae/voice-agent/server.py#L112-L129)**

The event ID is checked in `seen_livekit_events`, then `start_agent()` is
awaited, and only afterwards is the ID inserted. Two overlapping deliveries of
the same signed LiveKit event can both pass the check and both create a bot
session for the same room/caller. That produces competing participants and
duplicate processing for a carrier call.

Reserve the event atomically before the first await, remove the reservation on
startup failure, and add a concurrent duplicate-delivery test. A bounded,
TTL-backed store would also prevent the current in-memory set from growing for
the lifetime of the process; cross-worker/restart deduplication is a separate
deployment decision.

### P1 — broker disconnect can overwrite a terminal transfer with `CONSULTING`

**[`voice-agent/orchestrator.py:215-228`](https://github.com/e3-solutions/Frontline-Assignment/blob/f93f7a5f79efdf08c18c583a6a6f7167084b93ae/voice-agent/orchestrator.py#L215-L228)**,
**[`voice-agent/room2_pipeline_service.py:275-285`](https://github.com/e3-solutions/Frontline-Assignment/blob/f93f7a5f79efdf08c18c583a6a6f7167084b93ae/voice-agent/room2_pipeline_service.py#L275-L285)**, and
**[`voice-agent/orchestrator.py:288-293`](https://github.com/e3-solutions/Frontline-Assignment/blob/f93f7a5f79efdf08c18c583a6a6f7167084b93ae/voice-agent/orchestrator.py#L288-L293)**

After `dial_phone()` returns, a broker disconnect can schedule the Room2 callback,
which terminalizes the attempt as `FAILED/broker_disconnected`; the original
coroutine then continues without rechecking the attempt and writes `CONSULTING`.
The audit consequently reports an active-looking state after Room2 teardown and
Room1 restoration. Guard every post-await transition with an attempt generation
and terminal-state check, or centralize transitions in a compare-and-set state
machine. Test a disconnect interleaved between dial return and
`mark_broker_answered()` and assert the terminal result is preserved.

### P1 — cleanup never makes its documented second provider-operation attempt

**[`voice-agent/orchestrator.py:501-517`](https://github.com/e3-solutions/Frontline-Assignment/blob/f93f7a5f79efdf08c18c583a6a6f7167084b93ae/voice-agent/orchestrator.py#L501-L517)**

`_retry_cleanup()` iterates over attempts 1 and 2, but its exception path awaits
one event-loop turn and immediately returns `False`. Thus a transient first
failure deleting Room2, removing a broker, or deleting Room1 never reaches the
second attempt. The helper is used on all terminal paths, so a one-off provider
network error leaves the participant or room behind (and may leave stale Room2
state that blocks the next transfer).

Move `return False` outside the loop and add a test operation that raises once
then succeeds: assert two calls and successful cleanup. Existing transfer tests
exercise successful cleanup and call-level failures, but not a cleanup operation
that fails once.

### P2 — BotRunner startup can stall the webhook handler for the client default timeout

**[`voice-agent/server_utils.py:33-52`](https://github.com/e3-solutions/Frontline-Assignment/blob/f93f7a5f79efdf08c18c583a6a6f7167084b93ae/voice-agent/server_utils.py#L33-L52)**, reached from
**[`voice-agent/server.py:128`](https://github.com/e3-solutions/Frontline-Assignment/blob/f93f7a5f79efdf08c18c583a6a6f7167084b93ae/voice-agent/server.py#L128)**

`start_bot_local()` posts to the BotRunner without a per-request timeout while
the webhook awaits it inline. A process that accepts a TCP connection but never
returns headers holds the webhook for aiohttp's broad session default timeout,
causing provider retries and amplifying the duplicate-start race. Use a short explicit
`aiohttp.ClientTimeout`, return retryable `503` on expiry, and roll back the
idempotency reservation. Test against a non-responding local endpoint.

## Evidence and checks

* Reviewed the PR's 42 changed files and traced inbound event → local bot start,
  outbound API → SIP participant creation, and transfer → Room1 media gating →
  broker dialing.
* A prior concern about `LiveKitTransferMedia.gate_primary_media` was retracted:
  in Pipecat 0.0.95, `LiveKitInputTransport` inherits `BaseInputTransport` and
  then `FrameProcessor`, which supplies both awaited gating methods.
* [LiveKit's outbound-call documentation](https://docs.livekit.io/telephony/making-calls/outbound-calls/)
  states that `wait_until_answered=True` returns only after pickup and raises for
  rejection or no answer. Its [SIP API reference](https://docs.livekit.io/reference/telephony/sip-api/)
  also describes the option as returning after the call is answered.
* No provider credentials were used and no real calls were placed. The empty
  supplied assessment workspace prevented local test execution against the
  pinned checkout; conclusions above are source/API traces, not claimed runtime
  reproductions.

## Assumptions and unresolved release questions

* The PR describes one active bot per runner as intentional. That is not counted
  as a code defect here, but production must run an isolated runner per concurrent
  call (or change task ownership to be room/call keyed); otherwise a second call
  gets the runner's conflict response.
* LiveKit/Telnyx routing, signed webhook provisioning, speech services, Supabase
  recording storage, and real PSTN transfer behavior still require development
  qualification after the blockers are fixed.
* Process-local webhook deduplication does not survive restart or coordinate
  workers. Whether that is acceptable depends on the stated single-runner
  deployment model; it should be made explicit before scaling the service.
