# Secure Socket-Based Smart Intersection Control System

MSE806 Intelligent Transportation Systems, Assessment 2 (software part).

A four-way intersection controller server. Vehicle clients connect over TCP, report their positions and obey the signal. The controller runs **fixed-time** or **adaptive (actuated)** signal timing, and a legitimate **ambulance** can pre-empt the signal. A **Tkinter dashboard** shows everything live, and an **attacker** script tests forged emergency requests, replay, tampering, fake signals and flooding, with security **off** and **on**.

Pure Python standard library only: no `pip install` needed.

## Requirements

- Python **3.10+** (macOS or Linux).
- Tk, for the dashboard. Check it with:
  ```
  python3 -m tkinter
  ```
  A small window should open. If you get an error on macOS, install Python from python.org (that installer includes Tk), or with Homebrew run `brew install python-tk`.

## Setup

```
cd smart_intersection
python3 tools/gen_keys.py          # writes keys.json (random pre-shared demo keys)
```

`keys.example.json` shows the format. Never commit a real `keys.json`.

## One-click demo (recommended)

```
python3 demo_app.py
```

This opens a single window with the live dashboard on the left and a control panel on the right:

| Control | What it does |
|---|---|
| **Security** ON / OFF, **Signal** Fixed / Adaptive, **Traffic** preset, **Speed** 1× / 2× / 4× / 8× | settings, applied when you press Start |
| **▶ Start / Restart system** | (re)starts the server and the traffic generator with those settings |
| **■ Stop all** | stops every process |
| **🚑 Send ambulance** | sends AMB01 on an approach that is currently red; its messages are recorded for A2/A3 |
| **⟳ Restart server** | stops the server for 3 s, then starts it again; vehicles and the dashboard reconnect by themselves |
| **A1 … A5** | runs that attack against the current security mode (A2/A3 need the ambulance to have passed first) |

The **operation log** explains each click, shows the expected and actual result of every attack (`RESULT: BLOCKED` in green, `RESULT: SUCCEEDED` in red), and streams live server events: pre-emption start/end, fallbacks and every rejected message.

**Speed-up:** at 2×/4×/8× the traffic and the signal timings run that many times faster. The dashboard shows an orange **▶▶ 4× SPEED** badge with the simulated time, and the *Speed* row reads "4× (accelerated)". Times on the dashboard (phase time, average wait) are in simulated seconds. Security timing (2 s freshness window, rate limit) always stays in real time.

## Run the demo from the command line

```
python3 run_demo.py --security on --signal adaptive --traffic asymmetric --speed 4
python3 run_demo.py --security on --signal adaptive --traffic asymmetric
python3 run_demo.py --security on --signal adaptive --traffic asymmetric --ambulance-at 30
python3 run_demo.py --security off --signal fixed --traffic asymmetric
```

`run_demo.py` starts the server, the dashboard and the traffic generator (plus the ambulance if you ask for it). **Ctrl+C** stops them all. Other options: `--traffic balanced_low|balanced_high|asymmetric`, `--speed`, `--port`, `--duration`, `--seed`, `--no-gui`.

Each part can also be run on its own:

```
python3 -m server.intersection_server --security on --signal adaptive
python3 -m dashboard.gui --security on
python3 -m clients.traffic_generator --preset asymmetric --duration 300 --security on
python3 -m clients.traffic_generator --rates N=400,S=400,E=150,W=150 --security on
python3 -m clients.vehicle_agent --id V0001 --approach N --security on
python3 -m clients.emergency_vehicle --approach E --security on --record captured.jsonl
```

Reconnect demo: kill the server (Ctrl+C in its terminal) and start it again within a few seconds. The vehicles and the dashboard reconnect by themselves.

## Run the attacks live

Start a demo first. Use the same `--security` value as the server is running with.

```
python3 -m attacker.attacker --scenario A1 --security on     # fake ambulance AMB99
python3 run_demo.py ... --ambulance-at 10                    # A2/A3 need captured.jsonl from the real ambulance
python3 -m attacker.attacker --scenario A2 --security on     # replay captured messages (≥10 s old)
python3 -m attacker.attacker --scenario A3 --security on     # tamper with a captured, signed message
python3 -m attacker.attacker --scenario A4 --security on     # fake SIGNAL_STATE injected into a vehicle client
python3 -m attacker.attacker --scenario A5 --security on     # flood 2,000 messages on one connection
python3 -m attacker.attacker --scenario A5 --security off --sustain 10   # keep flooding for 10 s
```

Rejected messages appear in the dashboard's **security event log** and in `logs/security_events.csv`.

## Tests

```
python3 -m unittest discover -s tests -v
```

There are 46 tests: protocol, security, controller (including a two-hour randomised safety-invariant run), vehicle model, simulation, and integration tests against a real server on a free port.

## Experiments (each writes CSV to `results/`)

```
python3 -m experiments.exp_signal_performance   # fixed vs adaptive, 3 demands x 5 seeds; ambulance pre-emption  (~35 s)
python3 -m experiments.exp_latency              # PING RTT with 10/50/100 clients, security off/on              (~1 min)
python3 -m experiments.exp_hmac_overhead        # sign/verify cost, 100,000 repetitions                         (~25 s)
python3 -m experiments.exp_reliability          # reconnect, 1,000 malformed lines, fail-safes                  (~40 s)
python3 -m experiments.exp_attacks              # A1–A5 (+A2b, A5s) with security off and on                    (~1.5 min)
python3 -m experiments.make_charts              # results/*.csv -> results/*.svg
```

Method and measured results are in `docs/EXPERIMENTS.md`.

## Suggested 5-minute demo script (presentation video)

1. **Fixed time** (`run_demo.py --security on --signal fixed --traffic asymmetric`). Point out the long queues building on N/S while E/W gets 30 s of green it barely uses.
2. **Adaptive** (restart with `--signal adaptive`). Greens now follow demand (gap-out/max-out), N/S queues clear faster, and the *Average wait* on the panel drops (simulation: about 11.4 s → 6.5 s for this demand).
3. **Ambulance** (`--ambulance-at 20`). Pre-emption shows *ACTIVE (E)*. The conflicting green goes through yellow and all-red, then E turns green and the flashing red ambulance passes without stopping.
4. **Security OFF** (`run_demo.py --security off …`, then `attacker --scenario A1 --security off`). The fake ambulance AMB99 takes the green.
5. **Security ON** (`run_demo.py --security on … --ambulance-at 10`, then A1 and A2). Both are rejected (`UNAUTHORISED`, `BAD_SIGNATURE`, `STALE`) and appear in the red security log on the dashboard. No pre-emption happens.

## Project structure

```
smart_intersection/
├── config.py              all tunable constants
├── demo_app.py            one-window demo control panel (buttons + operation log)
├── run_demo.py            launches server + dashboard + traffic (+ ambulance)
├── keys.example.json      key file format (run tools/gen_keys.py for real keys)
├── common/                protocol, security, vehicle_model, net, logging_utils
├── server/                controller.py (pure logic), intersection_server.py (TCP)
├── clients/               vehicle_agent, traffic_generator, emergency_vehicle
├── dashboard/gui.py       Tkinter observer
├── attacker/attacker.py   attack scenarios A1–A5
├── experiments/           sim.py, exp_*.py, svg_chart.py, make_charts.py, harness.py
├── tools/gen_keys.py
├── tests/                 unittest suites
├── docs/                  ARCHITECTURE.md, PROTOCOL.md, EXPERIMENTS.md
├── results/               generated CSV + SVG
└── logs/                  generated logs
```
