# Communication Protocol

## Transport and framing

- TCP, one connection per client. Default `127.0.0.1:9000` (`--host`, `--port`).
- **Newline-delimited JSON**: one UTF-8 JSON object per line.
- Maximum message size **4,096 bytes** for everything a client sends. A longer line is answered with `ERROR{code:"MALFORMED"}` and the connection is closed.
- Invalid JSON, a missing field, a wrong field type, an unknown `type` or a bad enum value (`role`, `approach`) gets `ERROR{code:"MALFORMED"}`. The connection stays open, and a bad message never crashes the server (the per-message handler also catches unexpected exceptions).
- Server → observer `SNAPSHOT` messages list every vehicle, so with 100+ vehicles they exceed 4 KB. Observers therefore accept up to `MAX_SNAPSHOT_BYTES = 65,536`. The 4 KB limit still applies to everything the server *receives*, and that is the limit that protects the server.

## Envelope (every message, both directions)

```json
{"v": 1, "type": "POSITION_UPDATE", "sender": "V0007", "ts": 1730000000.123,
 "nonce": "9f2c1a7b3e4d5f60", "payload": {...}, "sig": "hex-hmac-sha256"}
```

| Field | Meaning |
|---|---|
| `v` | protocol version (1) |
| `type` | message type (below) |
| `sender` | client ID (`V0001`, `AMB01`, `DASH1`, …) or `SERVER` |
| `ts` | `time.time()` when the message was built. All processes run on one machine in the demo, so the clocks agree. A real deployment needs synchronised clocks (GNSS/NTP), as V2X systems already assume |
| `nonce` | `secrets.token_hex(8)`, unique per message |
| `payload` | type-specific object |
| `sig` | HMAC-SHA256 (hex) over the **canonical JSON** of the envelope without `sig`: `json.dumps(obj, sort_keys=True, separators=(",", ":"))` |

With security **off**, `sig` may be missing or empty and is ignored.

## Message types

| Type | Direction | Payload | Notes |
|---|---|---|---|
| `REGISTER` | C→S | `role` (`vehicle`/`emergency`/`observer`), `approach` (N/S/E/W, not for observers), `resume` (bool) | First message on every connection |
| `REGISTER_ACK` | S→C | `accepted`, `reason`, `security_mode`, `signal_mode` | Followed immediately by a `SIGNAL_STATE` |
| `POSITION_UPDATE` | C→S | `approach`, `distance_m` (negative after crossing), `speed_mps`, `state` (`moving`/`stopped`) | Every 0.5 s |
| `QUEUE_INFO` | S→C | `ahead` (int), `ahead_distance_m` (float or null) | Reply to each `POSITION_UPDATE` (see below) |
| `CROSSED` | C→S | `approach`, `wait_time_s`, `travel_time_s` | Sent once, then the client disconnects |
| `EMERGENCY_REQUEST` | C→S | `approach`, `eta_s` | Honoured only from an authorised emergency sender |
| `EMERGENCY_CLEAR` | C→S | `approach` | Sent once the ambulance is past the stop line |
| `PING` / `PONG` | C→S / S→C | `t_send` / `t_send` (echoed), `t_server` | Latency measurement |
| `HEARTBEAT` | C→S | — | Sent when nothing else was sent for 1 s |
| `SIGNAL_STATE` | S→all | `phase`, `ns`, `ew` (`green`/`yellow`/`red`), `phase_elapsed_s`, `signal_mode`, `preemption` (null or approach) | 2 Hz **and** immediately on every phase, pre-emption or mode change |
| `SNAPSHOT` | S→observers | `signal`, `vehicles` (`{id, approach, distance_m, state, emergency}`), `queues` (`{N,S,E,W}`), `stats` (`avg_wait_s`, `crossed`, `connected`, `security_mode`, `rejected`), `security_events` (last 10) | 10 Hz |
| `ERROR` | S→C | `code`, `reason` | `MALFORMED`, `BAD_SIGNATURE`, `STALE`, `REPLAY`, `RATE_LIMITED`, `UNAUTHORISED`, `NOT_REGISTERED` |

### QUEUE_INFO: car-following in the live system

Live clients cannot see each other, so the server tells each vehicle about the vehicle directly ahead of it on the same approach:

- `ahead`: the number of vehicles ahead on this approach (closer to or past the stop line).
- `ahead_distance_m`: the **`distance_m` of the nearest vehicle ahead**, as it last reported it, or `null` if the road is clear.

The client keeps `MIN_GAP_M = 7 m` (5 m car + 2 m) behind that position. A stopped queue therefore forms at the line, then at 7 m, 14 m and so on. The reported position can be up to 0.5 s old. A vehicle ahead only ever moves forward, so stale data is conservative and can only make the follower brake earlier. Ties (two vehicles spawned at 200 m at the same moment) are broken by registration order.

## Server processing pipeline (security on)

```
line → size check → JSON + schema validation → token bucket (rate limit)
     → which key verifies? (emergency key for that ID, else fleet key, else BAD_SIGNATURE)
     → freshness |now - ts| ≤ 2 s (STALE) → nonce not seen before (REPLAY)
     → authorisation: claims emergency? must be proven by the emergency key (UNAUTHORISED)
                      sender must match the registered session ID (UNAUTHORISED)
     → registered? (NOT_REGISTERED) → dispatch
```

The signature is checked before the nonce cache is updated, so unsigned junk cannot fill the cache. Every rejection is written to `logs/security_events.csv` (`wall_time, sender, peer_addr, msg_type, reason`), and the last 10 are included in `SNAPSHOT`s.

Server → client messages are signed with the **server key** when security is on. Vehicle clients verify every message and ignore a `SIGNAL_STATE` that fails verification, keeping the last valid one.

## Timing and reliability parameters

| Parameter | Value |
|---|---|
| Position period / heartbeat | 0.5 s / 1 s |
| Client signal timeout (treat as red) | 3 s |
| Session grace (server keeps a disconnected vehicle) | 10 s |
| Reconnect back-off | 0.5, 1, 2, 4, then 5 s; re-register with `resume: true` and the same ID |
| Freshness window / nonce TTL | 2 s / 4 s |
| Rate limit | 20 msg/s, burst 40; 3 violations in 10 s → disconnect |
| Per-client outgoing queue | 500 messages; a client that cannot keep up is dropped |
