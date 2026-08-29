# All-Stop

A self-hosted, file-backed kill switch and webhook broadcast for stopping agent tooling org-wide
— without any Northstar-operated infrastructure in the path.

Every guard in this portfolio (MCP Gateway's `ToolCallLimiter`, Leviathan Platform's
`BudgetGuard`) is in-memory, per-process, reset on restart. There's no way for a human to say
"stop every agent right now" across processes — only per-run limits an agent can trip on its
own. All-Stop is that missing piece: a human-triggered, cross-process stop any guard can check
cheaply on every call, plus a webhook fan-out so the right people find out immediately.

## Install

```bash
pip install -e .
```

Registers both the `all_stop` package and an `all-stop` console command.

## As a library

```python
from all_stop import KillSwitch

switch = KillSwitch("killswitch.json", webhook_urls=("https://hooks.slack.com/services/...",))

if switch.tripped():
    raise RuntimeError("agent tooling is stopped: " + switch.status().reason)

# ...later, from an incident:
switch.trip("compromised MCP server, investigating", actor="erik")
# ...and once resolved:
switch.reset(actor="erik")
```

Any guard object can wire this in as one extra check — see the MCP Gateway integration for a
real example: `McpLifecycleGuard(..., kill_switch=switch)` denies every call while tripped, with
the trip reason surfaced in the denial.

### Pause — a distinct, softer state (0.2+)

AIUC-1's C009 kill-switch requirement asks for human-in-the-loop pause/redirect "without
requiring full technical shutdown" — a state distinct from a full trip. `pause()` is that state:

```python
switch.paused()                                  # True while paused, independent of tripped()
switch.pause("reviewing a suspicious tool call", actor="erik")
switch.reset(actor="erik")                       # clears from either paused or tripped
```

Honest about what this does and doesn't change: every integration in this portfolio
(`McpLifecycleGuard`, Iron-Thread's `EgressPolicy`) denies a call the same way while paused as
while tripped, because none of them has a review-queue/redirect mechanism to route a paused call
to instead — there is no softer *enforcement* yet. What pause buys today is a genuinely distinct,
separately-timestamped, separately-audited state: your own status/webhook/log output can tell
"full stop, compromised" apart from "hold for review," even though both currently deny. A
`kill_switch` object that implements only `tripped()` (the pre-0.2 contract) is completely
unaffected — `paused()` is checked via `getattr`, never assumed to exist.

## As a CLI

```bash
all-stop trip   --evidence killswitch.json --reason "compromised MCP server, investigating" --actor erik --webhook https://hooks.slack.com/services/...
all-stop pause  --evidence killswitch.json --reason "reviewing a suspicious tool call" --actor erik
all-stop status --evidence killswitch.json   # exit code: 0 clear, 1 tripped, 2 paused - usable as a script/CI gate
all-stop reset  --evidence killswitch.json --actor erik   # clears from either tripped or paused
```

## How the multi-process story actually works

State lives in one JSON file. `tripped()` reads it fresh from disk every call — no daemon, no
polling interval to tune, no shared in-memory state. A shared filesystem path (a local disk, a
network share, a synced folder) is the entire mechanism. Writes are atomic (temp file +
`os.replace`), so a crash mid-write can never leave a reader looking at a corrupt file.

## Honest about scope

- **Not a network service.** There's no daemon, no port, nothing listening. Every check is a
  file read; every trip/reset is a file write. This is deliberate — it matches every other
  product in this portfolio's self-hosted-only, zero-telemetry promise. Nothing here ever
  leaves your infrastructure unless you configure a webhook URL that does.
- **A corrupt or unreadable state file is treated as tripped**, not clear — the opposite failure
  direction from almost everything else in this portfolio, because for a kill switch
  specifically, a silent false "not tripped" is the dangerous outcome.
- **Webhook delivery is best-effort.** A dead or slow webhook (5-second timeout) never blocks or
  fails a trip/reset — the state change has already happened by the time the broadcast fires.
  `send_broadcast()` returns a bool if a caller wants to check delivery itself.
- **The Slack/Teams format detection is a URL-substring heuristic**, not real content
  negotiation. A webhook proxy or custom relay in front of one of those services won't match,
  and falls through to the generic structured-JSON shape — the safer default for anything
  unrecognized.
- **This does not retrofit every product in the portfolio.** Wired into MCP Gateway's
  `McpLifecycleGuard` and Iron-Thread's `EgressPolicy` (leviathan-platform + agent-guardrails)
  as of 0.2. Probe Kit and Decoy Kit are natural next candidates, not yet done.
- **`pause()` (0.2+) is a real, distinct, separately-audited state — not yet a real, distinct
  enforcement policy.** Every current integration denies the same way while paused as while
  tripped, because none of them has anywhere else to route a paused call. See the "Pause" section
  above before assuming a customer's own tooling behaves differently under a pause than a trip -
  today it doesn't, only the audit trail does.
- **No dashboard, no event history, no aggregation across multiple kill-switch files.** Current
  state only — `status` tells you what's true right now, not a timeline of past trips/pauses.

## Tests

```bash
python -m unittest discover -s tests -v
```

Webhook tests run against a real local HTTP server, not a mock — the same discipline used
elsewhere in this portfolio for proving side effects actually happen rather than asserting on
stand-ins.
