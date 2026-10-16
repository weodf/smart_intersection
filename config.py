"""Central configuration for the smart intersection system.

Every tunable constant lives here. Command-line flags in the individual
programs override the most important ones.
"""

# --- Network -----------------------------------------------------------------
HOST = "127.0.0.1"          # default bind / connect address
PORT = 9000                 # default TCP port
MAX_MSG_BYTES = 4096        # maximum size of one newline-delimited JSON message
MAX_SNAPSHOT_BYTES = 65536  # server->observer SNAPSHOTs list every vehicle, so observers accept more
SEND_QUEUE_MAX = 500        # outgoing messages buffered per client before it is dropped as too slow
SEND_TIMEOUT_S = 1.0        # a client that cannot accept data within this time is dropped
PROTOCOL_VERSION = 1

# --- Signal timing (seconds) ---------------------------------------------------
YELLOW_S = 3.0              # yellow interval after every green
ALL_RED_S = 2.0             # all-red clearance after every yellow
FIXED_GREEN_S = 30.0        # green length in fixed-time mode
MIN_GREEN_S = 10.0          # adaptive: minimum green
MAX_GREEN_S = 45.0          # adaptive: maximum green (when there is conflicting demand)
GAP_S = 3.0                 # adaptive: gap-out threshold
DETECTION_ZONE_M = 60.0     # adaptive: detection zone upstream of the stop line
PREEMPT_MAX_HOLD_S = 30.0   # emergency pre-emption: maximum time the green is held

# --- Security ------------------------------------------------------------------
FRESHNESS_WINDOW_S = 2.0    # messages older/newer than this are STALE
RATE_LIMIT_PER_S = 20.0     # token bucket refill rate per connection
RATE_BURST = 40             # token bucket capacity
RATE_VIOLATIONS_MAX = 3     # this many violations ...
RATE_VIOLATION_WINDOW_S = 10.0  # ... within this window disconnects the client

# --- Reliability ---------------------------------------------------------------
SESSION_GRACE_S = 10.0          # server keeps a disconnected vehicle's state this long
CLIENT_SIGNAL_TIMEOUT_S = 3.0   # client treats the signal as red after this long without state
DETECTION_TIMEOUT_S = 10.0      # adaptive -> fixed fallback after this long without detection data
BACKOFF_STEPS_S = (0.5, 1.0, 2.0, 4.0)  # reconnect back-off sequence ...
BACKOFF_MAX_S = 5.0                     # ... capped at this value

# --- Server loop rates ---------------------------------------------------------
CONTROL_HZ = 10.0           # controller tick rate
SIGNAL_BROADCAST_HZ = 2.0   # periodic SIGNAL_STATE broadcast
SNAPSHOT_HZ = 10.0          # SNAPSHOT rate to observers

# --- Client loop rates -----------------------------------------------------------
CLIENT_TICK_HZ = 10.0       # vehicle kinematics update rate
POSITION_PERIOD_S = 0.5     # POSITION_UPDATE period
HEARTBEAT_PERIOD_S = 1.0    # HEARTBEAT when nothing else has been sent

# --- Vehicle kinematics ----------------------------------------------------------
SPAWN_DISTANCE_M = 200.0    # vehicles appear this far from the stop line
MAX_SPEED_MPS = 13.9        # 50 km/h
ACCEL_MPS2 = 2.0            # acceleration
DECEL_MPS2 = 4.0            # comfortable deceleration
MIN_GAP_M = 7.0             # 5 m car + 2 m gap
STOPPED_SPEED_MPS = 0.5     # below this speed a vehicle counts as waiting
CROSSED_PAST_M = 20.0       # a vehicle has crossed once it is this far past the line
EMERGENCY_REQUEST_DIST_M = 150.0  # ambulance requests pre-emption at this distance

# --- Approaches ------------------------------------------------------------------
APPROACHES = ("N", "S", "E", "W")
DIRECTION_OF = {"N": "ns", "S": "ns", "E": "ew", "W": "ew"}

# --- Files -----------------------------------------------------------------------
KEYS_FILE = "keys.json"
LOG_DIR = "logs"
RESULTS_DIR = "results"
