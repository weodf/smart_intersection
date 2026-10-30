# Experiments

Every number below was measured by running the scripts in this repository (final run on 27 Oct 2026, Python 3.12, Linux, all processes on one machine over localhost). Re-running gives slightly different latency figures. The simulation results are deterministic for a given seed.

Run all of them from the project root:

```
python -m experiments.exp_signal_performance
python -m experiments.exp_latency
python -m experiments.exp_hmac_overhead
python -m experiments.exp_reliability
python -m experiments.exp_attacks
python -m experiments.make_charts
```

---

## 1. Fixed-time vs adaptive signal control

**Purpose.** Measure whether actuated (gap-out/max-out) control reduces delay compared with fixed 30 s greens.

**Setup.** `experiments/sim.py` is a headless discrete-time simulation (0.1 s steps) that uses the **same** `SignalController` and `vehicle_model` as the live system, with no sockets. Poisson arrivals per approach. Demand presets: `balanced_low` (300 veh/h per approach), `balanced_high` (600), `asymmetric` (N/S 700, E/W 200). Each preset × mode is run with seeds 1–5, for 30 simulated minutes each, and the first 2 minutes are excluded as warm-up. Wait time = time spent below 0.5 m/s. Statistics cover vehicles that entered after the warm-up and crossed before the end. Avg queue = mean over time of stopped vehicles plus vehicles waiting to enter; max queue = largest single-approach queue. The safety invariant was asserted in every run (0 violations).

**Command / output.** `python -m experiments.exp_signal_performance` → `results/signal_performance.csv` (per seed), `results/signal_performance_summary.csv` (mean/SD), chart `results/avg_wait_fixed_vs_adaptive.svg`.

**Results (mean over 5 seeds; ± is the SD of the average wait):**

| Scenario | Mode | Avg wait (s) | p95 wait (s) | Max wait (s) | Avg queue (veh) | Max queue (veh) | Throughput (veh/h) |
|---|---|---|---|---|---|---|---|
| balanced_low | fixed | 10.64 ± 0.60 | 33.6 | 36.8 | 3.56 | 9.2 | 1189 |
| balanced_low | adaptive | 6.41 ± 0.69 | 25.1 | 47.6 | 2.13 | 8.6 | 1191 |
| balanced_high | fixed | 10.74 ± 0.39 | 33.9 | 37.1 | 7.23 | 15.0 | 2391 |
| balanced_high | adaptive | 12.98 ± 0.24 | 43.9 | 51.7 | 8.74 | 17.2 | 2406 |
| asymmetric | fixed | 11.38 ± 0.47 | 34.3 | 39.9 | 5.75 | 18.4 | 1779 |
| asymmetric | adaptive | 6.49 ± 0.39 | 30.6 | 49.0 | 3.29 | 12.8 | 1778 |



**Observations.**

- Adaptive control cut the average wait by about 40 % under low and asymmetric demand (10.6 → 6.4 s and 11.4 → 6.5 s).
- Under balanced high demand, adaptive was *worse* (10.7 → 13.0 s). Every approach always has demand, so greens rarely gap out and run to max-out (45 s). That gives a longer cycle than the fixed 30 s plan.
- Adaptive control also raises the *maximum* wait in every scenario (a long green on one side means a long red on the other).
- Throughput is the same in all cases, because the intersection is below saturation.

---

## 2. Emergency pre-emption

**Setup.** Same simulation, 15 simulated minutes. At t = 600 s an ambulance enters 200 m upstream on an approach that is red at that moment. With pre-emption on, it sends the request at 150 m. The conflicting green ends immediately but still runs its yellow (3 s) and all-red (2 s). Seeds 1–5, `balanced_high` and `asymmetric`, both signal modes.

**Output.** `results/preemption.csv`, chart `results/preemption_ambulance_wait.svg`.

| Scenario | Mode | Pre-emption | Ambulance wait (s) | Ambulance travel time (s) | Other vehicles' avg wait (s) |
|---|---|---|---|---|---|
| balanced_high | fixed | off | 15.76 | 38.28 | 11.21 |
| balanced_high | fixed | on | 0.00 | 17.54 | 11.42 |
| balanced_high | adaptive | off | 7.44 | 30.48 | 13.04 |
| balanced_high | adaptive | on | 0.00 | 21.06 | 13.74 |
| asymmetric | fixed | off | 16.58 | 39.74 | 11.11 |
| asymmetric | fixed | on | 0.00 | 18.60 | 11.43 |
| asymmetric | adaptive | off | 7.54 | 27.02 | 6.70 |
| asymmetric | adaptive | on | 0.00 | 16.58 | 6.79 |



**Observations.**

- Pre-emption removed the ambulance's stopped time in every run (from 7–17 s down to 0 s) and roughly halved its travel time through the 220 m section.
- Travel time stays above the free-flow value (≈ 15.8 s) because the ambulance follows vehicles queued ahead of it.
- The cost to other traffic over the run was small (+0.1 to +0.7 s average wait).

---

## 3. Communication latency

**Purpose.** Measure round-trip time and loss as the number of concurrent clients grows, with and without HMAC.

**Setup.** `exp_latency.py` starts the real server **as a separate process** on a free port (security off, then on). N ∈ {10, 50, 100} clients connect over TCP, register, synchronise on a barrier, and each sends 50 `PING`s at 5 Hz. RTT is measured from just before sending to receipt of the matching `PONG` (with security on, the PONG's server signature is also verified). A PING without a reply within 2 s counts as lost.

**Output.** `results/latency.csv`, chart `results/latency_by_clients.svg`.

| security | clients | mean_ms | p50_ms | p95_ms | p99_ms | max_ms | lost |
|---|---|---|---|---|---|---|---|
| off | 10 | 1.375 | 1.309 | 2.517 | 3.193 | 6.208 | 0 |
| off | 50 | 5.874 | 5.524 | 11.21 | 14.594 | 18.736 | 0 |
| off | 100 | 12.019 | 11.199 | 23.346 | 29.203 | 38.359 | 0 |
| on | 10 | 1.36 | 1.119 | 2.837 | 6.251 | 7.594 | 0 |
| on | 50 | 8.138 | 7.82 | 14.887 | 17.962 | 21.508 | 0 |
| on | 100 | 15.661 | 14.979 | 28.732 | 34.913 | 44.562 | 0 |



**Observations.**

- No messages were lost in any configuration.
- RTT grows roughly linearly with the number of clients. All clients run as threads in *one* Python process and send in lock-step, so part of this growth is client-side GIL contention, not server cost.
- Security on adds about 0–4 ms at the mean (≈ 30 % at 100 clients).

---

## 4. Cost of HMAC-SHA256

**Setup.** `timeit`, 100,000 repetitions, best of 3, on a typical `POSITION_UPDATE` envelope.

**Output.** `results/hmac_overhead.csv`, chart `results/hmac_overhead.svg`.

| operation | mean_us | ops_per_s |
|---|---|---|
| sign | 9.957 | 100427 |
| verify | 10.488 | 95350 |
| encode+decode (unsigned) | 11.373 | 87930 |
| full round trip unsigned (make+encode+decode) | 12.623 | 79217 |
| full round trip signed (make+sign+encode+decode+verify) | 38.787 | 25782 |
| security overhead per message (signed - unsigned) | 26.164 | 38220 |



**Observations.**

- Signing or verifying one message takes about 10 µs.
- A full signed round trip (build + sign + encode + decode + verify) costs about 26 µs more than the unsigned one.
- In the live system each vehicle sends about 2 messages per second, so 100 vehicles cost the server well under 1 % of one core for authentication.
- The signature adds 73 bytes (64 hex characters + field name) to a 181-byte message.

---

## 5. Reliability features

**Setup.** `exp_reliability.py`, server in-process with security on.

1. **Reconnect.** 20 vehicles are connected. The server is stopped, then restarted on the same port after 3 s.
2. **Malformed input.** 1,000 random malformed lines (seed 7): bad JSON, random bytes, missing fields, wrong types, unknown types, non-object JSON, and oversize lines (> 4,096 bytes, after which the server answers and closes the connection; the fuzzer reconnects). Then a normal client registers and pings.
3. **Signal-loss fail-safe.** One vehicle on a green approach, after which the server stops sending `SIGNAL_STATE` (test hook `withhold_signal`).
4. **Detection fail-safe.** Adaptive mode with a connected vehicle that sends no position data.
5. **Session resume.** The server drops a vehicle's TCP connection.

**Output.** `results/reliability.csv`.

| test | metric | value | passed |
|---|---|---|---|
| reconnect | vehicles_reregistered | 20/20 | True |
| reconnect | mean_s_after_restart | 0.511 | True |
| reconnect | max_s_after_restart | 0.53 | True |
| reconnect | max_total_outage_s | 3.531 | True |
| malformed_input | lines_sent | 1000 | True |
| malformed_input | rejected_by_server | 1000 | True |
| malformed_input | connections_closed_for_oversize | 119 | True |
| malformed_input | server_alive_and_serving | True | True |
| malformed_input | normal_client_rtt_ms_after | 0.532 | True |
| signal_loss_failsafe | light_was_green_before_cut | True | True |
| signal_loss_failsafe | seconds_until_client_treats_signal_as_red | 2.98 | True |
| signal_loss_failsafe | vehicle_stopped_at_or_before_line | True | True |
| signal_loss_failsafe | stop_position_m_before_line | 0.0 | True |
| signal_loss_failsafe | stays_stopped_while_withheld | True | True |
| signal_loss_failsafe | moves_again_when_state_returns | True | True |
| detection_fallback | seconds_until_fixed_fallback | 10.06 | True |
| detection_fallback | returns_to_adaptive_on_data | True | True |
| session_resume | seconds_to_resume | 0.59 | True |
| session_resume | server_kept_vehicle_state | True | True |



**Observations.**

- All 20 vehicles re-registered by themselves about 0.5 s after the server came back, which is the next step of the 0.5/1/2/4 s back-off. The total outage seen by the vehicles was 3.5 s.
- All 1,000 malformed lines were rejected and logged, and the server kept serving.
- After 3 s without valid signal state, the vehicle treated the signal as red and stopped exactly at the stop line (0.0 m). It moved on once state returned.

---

## 6. Security assessment (attacks A1–A5)

**Threat model.** The attacker can open TCP connections to the server and can read other clients' traffic. This is simulated by the real ambulance's `--record` option, which writes every message it sends to `captured.jsonl`: **this file stands in for an eavesdropper on an unencrypted network**. In A1/A5 the attacker is also an insider who holds the shared *fleet* key. It never has the server key or an emergency key.

**Setup.** `exp_attacks.py` runs each scenario against a fresh server process, first with `--security off` and then `--security on`. An observer connection watches the `SNAPSHOT`s to see whether pre-emption became active.

| ID | Scenario | How |
|---|---|---|
| A1 | Fake ambulance | Register as `role: emergency` with ID `AMB99` and send `EMERGENCY_REQUEST`: once signed with the fleet key, once unsigned |
| A2 | Replay | Resend the real ambulance's captured `REGISTER` and `EMERGENCY_REQUEST` ≥ 10 s later, unchanged |
| A2b | Immediate replay (extra) | Resend the same captured messages at once, inside the 2 s freshness window (tests the nonce cache) |
| A3 | Tampering | Change `approach` (E → N) in captured, validly signed messages and keep the old `sig` |
| A4 | Fake signal broadcast | Inject a `SIGNAL_STATE` saying N/S is green (unsigned, then signed with a wrong key) into a vehicle client's message handler, after a valid state saying N is red |
| A5 | Flooding | 2,000 pre-signed `HEARTBEAT`s as fast as possible on one connection while 10 normal clients ping at 5 Hz |
| A5s | Sustained flood (extra) | The same, but repeated for 10 s |

**Output.** `results/attacks.csv`, `results/flood.csv`, charts `results/attack_results.svg` and `results/flood_latency.svg`.

| Scenario | Security | Attempts | Accepted | Rejected | Rejection reasons | Pre-emption triggered |
|---|---|---|---|---|---|---|
| A1 | off | 4 | 4 | 0 | - | True |
| A1 | on | 4 | 0 | 4 | BAD_SIGNATURE:2;UNAUTHORISED:2 | False |
| A2 | off | 3 | 3 | 0 | - | True |
| A2 | on | 2 | 0 | 2 | STALE:2 | False |
| A2b | off | 2 | 2 | 0 | - | n/a |
| A2b | on | 2 | 0 | 2 | REPLAY:1;STALE:1 | n/a |
| A3 | off | 3 | 3 | 0 | - | True |
| A3 | on | 2 | 0 | 2 | BAD_SIGNATURE:2 | False |
| A4 | off | 2 | 2 | 0 | - | n/a |
| A4 | on | 2 | 0 | 2 | BAD_SIGNATURE:2 | n/a |
| A5 | off | 2000 | 2000 | 0 | - | n/a |
| A5 | on | 2000 | 39 | 3 | RATE_LIMITED:3 | n/a |
| A5s | off | 1444832 | 1444832 | 0 | - | n/a |
| A5s | on | 1066 | 39 | 3 | RATE_LIMITED:3 | n/a |



Normal clients' latency during the floods:

| Scenario | Security | Messages sent | Baseline p50/p95 (ms) | During flood p50/p95 (ms) | During max (ms) | Lost PINGs |
|---|---|---|---|---|---|---|
| A5 | off | 2000 | 0.62 / 4.54 | 0.57 / 1.86 | 2.52 | 0 |
| A5 | on | 2000 | 0.71 / 3.35 | 0.83 / 1.37 | 1.51 | 0 |
| A5s | off | 1444832 | 0.62 / 1.45 | 11.36 / 39.53 | 78.71 | 0 |
| A5s | on | 1066 | 0.71 / 1.59 | 0.57 / 2.12 | 2.73 | 0 |



**Observations.**

- **Security off:** every forged, replayed and tampered request was accepted and pre-emption was triggered (A1, A2, A3). The vehicle client accepted a fake green (A4).
- **Security on:** every attack message was rejected with the expected reason, and no pre-emption happened.
- **A1 fleet-key-signed attempt:** got `UNAUTHORISED`, because the server takes the role from the key that verifies the message, not from the `role` field. The unsigned attempt got `BAD_SIGNATURE`.
- **A2:** got `STALE`. **A2b:** the `EMERGENCY_REQUEST` got `REPLAY` (its timestamp was still fresh), and the `REGISTER`, captured earlier, got `STALE`.
- With security off, replaying the ambulance's `REGISTER` also **hijacked the real ambulance's session** (the server replaced its connection). The ambulance reconnected by itself, which is why the off-mode capture holds an extra `REGISTER` (A2 off = 3 messages).
- **Flooding:** a 2,000-message burst on localhost is processed in a fraction of a second, so it made no measurable difference in either mode (A5).
- Under the sustained flood with security off (A5s), the server processed 1.44 million messages in 10 s. Normal clients' median RTT rose from 0.62 to 11.4 ms (p95 1.45 → 39.5 ms, max 79 ms). With security on, the flooder was cut off after 39 accepted messages (burst of 40 minus its REGISTER) and 3 `RATE_LIMITED` replies, and normal latency did not change.

---

## Limitations

- **Localhost only.** Latency excludes real network delay, jitter and loss. All clients share one Python process in the latency test, so client-side contention inflates the RTT at 50–100 clients.
- **Simulated traffic.** One lane per approach, straight-through only, a simple car-following and dilemma-zone model, and no pedestrians. The adaptive controller is a basic actuated controller, not an optimising one (e.g. SCOOT/SCATS-style).
- **Detection from vehicle reports.** Adaptive control trusts the positions vehicles report. With the shared fleet key, a compromised vehicle could send false positions to bias the timing (not tested here).
- **Shared fleet key and pre-shared keys.** These stand in for per-vehicle certificates (IEEE 1609.2 / ECDSA, future work). Messages are authenticated but not encrypted.
- **Clock assumption.** Freshness checks assume synchronised clocks (true here because everything runs on one machine).
- **A4 is tested at the client.** The attacker cannot take over the server's TCP stream, so the fake signal is injected into the client's message handler (as the spec allows).
- **Wait-time statistics** include only vehicles that crossed before the end of each run.
