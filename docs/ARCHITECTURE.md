# Architecture

## Overview

```
 ┌──────────────┐  TCP / NDJSON   ┌──────────────────────────────────────────┐
 │ VehicleAgent │◄───────────────►│ IntersectionServer                        │
 │  × N threads │                 │  accept thread                            │
 ├──────────────┤                 │  per client: reader thread + writer thread│
 │ Ambulance    │◄───────────────►│  control loop 10 Hz ──► SignalController  │
 │  (AMB01 key) │                 │     (pure logic, injectable clock)        │
 ├──────────────┤                 │  security: HMAC, replay cache, buckets    │
 │ Dashboard    │◄── SNAPSHOT ────│  logs/security_events.csv, events.csv     │
 │  (observer)  │     10 Hz       └──────────────────────────────────────────┘
 ├──────────────┤                          ▲
 │ Attacker     │──── A1–A5 ───────────────┘
 └──────────────┘
```

| Module | Responsibility |
|---|---|
| `common/protocol.py` | envelope, canonical JSON, schema validation, line framing |
| `common/security.py` | HMAC-SHA256 sign/verify (`hmac.compare_digest`), `KeyStore` (role from key), `ReplayCache`, `TokenBucket` |
| `common/vehicle_model.py` | kinematics shared by the live clients and the fast simulation |
| `common/net.py` | client connection helper (sign, send, receive, verify) |
| `server/controller.py` | **pure** signal logic: phases, fixed/adaptive, pre-emption, fail-safe |
| `server/intersection_server.py` | TCP server, sessions, security pipeline, broadcasts, logging |
| `clients/*` | vehicle agent (reconnect, fail-safe), Poisson traffic generator, ambulance |
| `dashboard/gui.py` | Tkinter observer; the network thread hands data to Tk through a `queue.Queue` |
| `attacker/attacker.py` | attack scenarios A1–A5 |
| `experiments/*` | fast simulation, experiments, SVG charts |

## Design decisions

**Pure controller.** `SignalController` has no sockets, threads or clock. The live server calls it with `time.time()`. The headless simulation calls it with simulated time at 0.1 s steps, so 30 simulated minutes run in about a second. Because both use the same code, the safety invariant can be unit-tested over two simulated hours of random demand and random pre-emptions.

**Safety invariant.** Phases always follow `NS_GREEN → NS_YELLOW → ALL_RED → EW_GREEN → EW_YELLOW → ALL_RED`. Pre-emption can only (a) cut the *current* green short, which always goes to its yellow first, or (b) choose which green follows an all-red. Yellow (3 s) and all-red (2 s) can never be skipped or shortened. Transitions are timestamped at their exact due time, but never earlier than the previous tick, so a phase is never shortened in real time.

**Adaptive control (actuated).** A green ends after `MIN_GREEN` (10 s) when there is demand on the conflicting direction and either no vehicle has been in the 60 m detection zone of the green approaches for `GAP` (3 s) (gap-out), or `MAX_GREEN` (45 s) has been reached (max-out). With no conflicting demand the green rests. Detector inputs are derived from the vehicles' `POSITION_UPDATE`s: queue = stopped vehicles before the line, zone = vehicles within 60 m.

**Concurrency.** Shared state (vehicles, controller, replay cache) is protected by one `RLock`, and critical sections are short. Each client has a reader thread and a writer thread. The writer drains a bounded queue and batches pending messages into one `sendall`. A full queue means the client cannot keep up, so it is dropped. A slow client therefore never blocks the control loop or other clients. A broadcast message is encoded and signed once and then queued to every client.

**Reliability features.**

| Feature | Where |
|---|---|
| Reconnect with exponential back-off (0.5 → 5 s), `resume: true` with the same ID | `VehicleAgent` |
| Session grace: the server keeps a disconnected vehicle's state for 10 s | server control loop |
| Client fail-safe: no valid `SIGNAL_STATE` for 3 s → treat the signal as red | `VehicleAgent.light()` |
| Server fail-safe: adaptive mode with no detection data for 10 s → fixed-time (logged), back to adaptive when data returns | `SignalController.tick()` |
| Malformed/oversize input never crashes the server; per-message exception guard | server reader |
| Slow-client protection (bounded send queue, send timeout) | `Session` |
| Dashboard auto-reconnect ("Disconnected – retrying…") | `dashboard/gui.py` |

## Security design

| Threat | Mechanism | Error code |
|---|---|---|
| Spoofed or forged messages | HMAC-SHA256 over the canonical envelope, constant-time compare | `BAD_SIGNATURE` |
| Tampering in transit | same: any changed byte breaks the MAC | `BAD_SIGNATURE` |
| Fake ambulance (privilege escalation) | the role is taken from **which key verifies**, never from the `role` field; emergency actions need that ID's own emergency key | `UNAUTHORISED` |
| Identity hijack on a session | `sender` must match the ID the session registered with | `UNAUTHORISED` |
| Replay | ±2 s freshness window + per-sender nonce cache (TTL 4 s) | `STALE` / `REPLAY` |
| Flooding / DoS | per-connection token bucket (20/s, burst 40); 3 violations in 10 s → disconnect | `RATE_LIMITED` |
| Fake signal broadcast to vehicles | server→client messages signed with the server key; clients keep the last valid state | (client-side rejection) |
| Accountability | every rejection is logged with time, sender, peer address, type and reason | — |

With `--security off` all of these checks are disabled and self-declared roles are trusted. This deliberately vulnerable baseline is used to show the effect of each defence.

### Simplifications (stated honestly)

- **Pre-shared symmetric keys** from `keys.json`. All normal vehicles share one *fleet key*, so a compromised vehicle can impersonate any other *normal* vehicle (but not an ambulance and not the server). The `sender`-must-match-session rule limits this only within one connection.
- **No confidentiality.** Messages are signed, not encrypted. An eavesdropper can read positions (the ambulance's `--record` option simulates this), which is a privacy issue in a real deployment.
- **Clock assumption.** Freshness relies on synchronised clocks; the demo runs on one machine.
- **Future work:** certificate-based PKI with ECDSA signatures and pseudonym certificates in the style of **IEEE 1609.2** / SCMS. This would give per-vehicle identities, revocation and privacy-preserving pseudonyms, and it would replace the shared fleet key. TLS or DTLS could add confidentiality on the vehicle–infrastructure link.

## Threading model and files

| Thread | Count |
|---|---|
| accept loop | 1 |
| control loop (10 Hz tick, 2 Hz `SIGNAL_STATE`, 10 Hz `SNAPSHOT`, nonce purge) | 1 |
| reader + writer per client | 2 × clients |

Generated files: `logs/security_events.csv`, `logs/events.csv` (register, pre-emption start/end with sender, fallback, disconnects), and `results/*.csv|svg` from the experiments.
