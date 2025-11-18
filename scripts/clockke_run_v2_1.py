#!/usr/bin/env python3
"""
clockke_run_v2_1.py
Shunyaya Symbolic Mathematical Clock Kernel (SSM-ClockKe)
App identity: clockke (CLI version)

CLI version v2.1 (aligned with the SSM-ClockKe v2.1 specification):
- Same numeric kernel as v2.0 (no change to U, W, final_align, or band logic)
- Headless loop with UTC, final_align, band, stamp per tick
- (U, W) kernel with bounded alignment lane
- Tamper-evident stamp chain (SHA-256 based)
- Export CSV with tick_ms, dt_ms, a_stress (here a_stress := 0.0)
- Real-entropy alignment source based on dt_ms jitter + micro-noise
- Simple band classification and console log per tick
- Optional max tick count via --ticks (otherwise run until Ctrl+C)

Licensing.
- SSM-ClockKe is released as an open-standard kernel: free to implement or adapt
  with no registration and no fees, provided strictly as-is with no warranty,
  no endorsement, and no claim of exclusive stewardship.
- The core time standards SSM-Clock and SSM-Clock Stamp remain under their
  existing CC BY 4.0 research licenses.
"""

import time
import math
import hashlib
import csv
import random
import argparse
from datetime import datetime, timezone

# ---------------------------
# Manifest-style parameters
# ---------------------------

MANIFEST_ID = "CLOCKKE.CLI.DEFAULT.V2_1"

EPS_A = 1e-6       # clamp margin for a_raw
EPS_W = 1e-9       # avoid division by zero in W

# Tick cadence (seconds). Can be overridden via CLI.
TICK_SEC_DEFAULT = 1.0
W_DEFAULT = 1.0    # weight per tick

# Exponential decay factor for past evidence
DECAY_W = 0.995    # set to 1.0 to disable decay

# Real-entropy alignment parameters
BASELINE_A = 0.02          # baseline stability
JITTER_GAIN = 0.15         # how strongly jitter moves alignment
FREEZE_MULT = 1.5          # dt_ms > FREEZE_MULT * tick_ms => freeze penalty
FREEZE_PENALTY = 0.05      # penalty applied on serious freeze
NOISE_AMPL = 0.01          # amplitude of random micro-noise in [-NOISE_AMPL,+NOISE_AMPL]

# Auto-stop safety default (can be overridden via CLI)
AUTO_STOP_TICKS_DEFAULT = 0  # 0 => run until Ctrl+C

CSV_BASENAME = "stamps_clockke_cli"

# How many recent points to show in the history sparkline
HISTORY_LEN = 24

# RNG for micro-noise (system-level entropy)
rng = random.SystemRandom()

# ---------------------------
# Helper functions
# ---------------------------

def clamp_a(a):
    """Clamp a into (-1 + EPS_A, +1 - EPS_A)."""
    lo = -1.0 + EPS_A
    hi =  1.0 - EPS_A
    if a < lo:
        return lo
    if a > hi:
        return hi
    return a

def atanh_safe(x):
    """
    atanh(x) using log1p for better numeric behaviour.
    Assumes |x| < 1.
    """
    return 0.5 * (math.log1p(x) - math.log1p(-x))

def classify_band(a_out):
    """
    Band classification for real-entropy ClockKe v2.1.

    - Around 0 ([-0.10, +0.10]) => calm band "C"
    - Positive side => B, A, A+ as alignment rises
    - Negative beyond -0.10 => D (unusual / overspeed / concern)
    """
    if a_out >= 0.80:
        return "A+"
    elif a_out >= 0.40:
        return "A"
    elif a_out >= 0.10:
        return "B"
    elif a_out >= -0.10:
        return "C"
    else:
        return "D"

def now_utc_iso():
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def now_utc_stamp_for_filename():
    """Return compact UTC timestamp for filenames."""
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")

def source_a_raw(now_epoch, dt_ms, tick_ms, rng_obj):
    """
    Real-entropy alignment source for CLI v2.1.

    a_raw is driven by:
    - baseline stability,
    - how far dt_ms deviates from tick_ms,
    - explicit freeze penalties,
    - small random micro-noise.

    Steps:
    1) jitter = (dt_ms - tick_ms) / max(tick_ms, 1.0)
       - positive jitter => tick is late  -> more negative alignment
       - negative jitter => tick is early -> more positive alignment
    2) jitter_term = -JITTER_GAIN * jitter
    3) if dt_ms > FREEZE_MULT * tick_ms: apply FREEZE_PENALTY
    4) noise_term = NOISE_AMPL * noise_unit, noise_unit in [-NOISE_AMPL,+NOISE_AMPL]
    5) a_raw = BASELINE_A + jitter_term + noise_term
    """

    # Fallback for the very first tick when dt_ms may be 0.0
    if dt_ms <= 0.0 or tick_ms <= 0.0:
        jitter = 0.0
    else:
        jitter = (dt_ms - float(tick_ms)) / float(max(tick_ms, 1.0))

    base = BASELINE_A

    # Jitter term: late ticks (jitter > 0) reduce alignment, early ticks slightly increase it
    jitter_term = -JITTER_GAIN * jitter

    # Freeze penalty for serious delays
    if tick_ms > 0.0 and dt_ms > FREEZE_MULT * float(tick_ms):
        base -= FREEZE_PENALTY

    # Small symmetric noise in [-NOISE_AMPL, +NOISE_AMPL]
    noise_unit = rng_obj.uniform(-1.0, 1.0)
    noise_term = NOISE_AMPL * noise_unit

    return base + jitter_term + noise_term

def make_stamp(prev_stamp, payload_str, time_utc_str):
    """
    Chain rule:
    stamp_k := sha256(prev_stamp || sha256(payload_bytes) || time_utc_str)
    """
    payload_bytes = payload_str.encode("utf-8")
    h_payload = hashlib.sha256(payload_bytes).hexdigest().encode("utf-8")
    prev_bytes = (prev_stamp or "").encode("utf-8")
    time_bytes = time_utc_str.encode("utf-8")
    raw = prev_bytes + h_payload + time_bytes
    return hashlib.sha256(raw).hexdigest()


def spark_char(a):
    """
    Map alignment a in [-1,+1] to a simple ASCII symbol:

    - a <= -0.40          -> 'v'  (strong negative / overspeed)
    - -0.40 < a < -0.10   -> '/'  (mild negative)
    - -0.10 <= a <= 0.10  -> '_'  (calm / near zero)
    - 0.10 < a <= 0.40    -> '-'  (mild positive stress)
    - a > 0.40            -> '^'  (high positive stress)
    """
    if a <= -0.40:
        return "v"
    elif a < -0.10:
        return "/"
    elif a <= 0.10:
        return "_"
    elif a <= 0.40:
        return "-"
    else:
        return "^"

# ---------------------------
# Core run loop (CLI)
# ---------------------------

def run_clockke_cli(tick_sec, max_ticks):
    """
    Run clockke CLI loop with cadence tick_sec.
    If max_ticks > 0, stop after that many ticks,
    otherwise run until Ctrl+C.
    """
    tick_ms = float(tick_sec) * 1000.0

    U = 0.0
    W = 0.0
    prev_stamp = ""
    tick_index = 1
    rows = []
    last_tick_epoch = None
    history = []  # recent final_align values for ASCII sparkline

    print("clockke CLI v2.1 — real-entropy kernel")
    print(f"manifest_id      = {MANIFEST_ID}")
    print(f"tick_sec         = {tick_sec:.3f} (tick_ms = {tick_ms:.1f})")
    print(f"W_DEFAULT        = {W_DEFAULT:.3f}")
    print(f"DECAY_W          = {DECAY_W:.6f}")
    print(f"BASELINE_A       = {BASELINE_A:+.3f}")
    print(f"JITTER_GAIN      = {JITTER_GAIN:.3f}")
    print(f"FREEZE_MULT      = {FREEZE_MULT:.3f}")
    print(f"FREEZE_PENALTY   = {FREEZE_PENALTY:.3f}")
    print(f"NOISE_AMPL       = {NOISE_AMPL:.3f}")
    if max_ticks > 0:
        print(f"max_ticks        = {max_ticks}")
    else:
        print("max_ticks        = (infinite, use Ctrl+C to stop)")
    print("Press Ctrl+C to interrupt.\n")

    try:
        while True:
            loop_start = time.time()
            now_epoch = loop_start
            time_utc = now_utc_iso()

            # dt_ms calculation
            if last_tick_epoch is None:
                # Treat first tick as if it matched the planned cadence
                dt_ms = tick_ms
            else:
                dt_ms = (now_epoch - last_tick_epoch) * 1000.0
            last_tick_epoch = now_epoch

            # 1. Get alignment source from real entropy
            a_raw = source_a_raw(now_epoch, dt_ms, tick_ms, rng)

            # 2. U/W kernel with exponential decay
            a_c = clamp_a(a_raw)
            u = atanh_safe(a_c)

            U = DECAY_W * U + W_DEFAULT * u
            W = DECAY_W * W + W_DEFAULT

            denom = W if W > EPS_W else EPS_W
            a_out = math.tanh(U / denom)

            # 3. Band
            band = classify_band(a_out)

            # 4. Payload + stamp
            final_align_str = f"{a_out:+.9f}"
            payload_str = f"{time_utc}|{final_align_str}|{band}"
            stamp = make_stamp(prev_stamp, payload_str, time_utc)
            prev_stamp = stamp

            # 5. Record row (a_stress := 0.0 for CLI)
            rows.append(
                (
                    tick_index,
                    time_utc,
                    final_align_str,
                    band,
                    stamp,
                    tick_ms,
                    dt_ms,
                    0.0,  # a_stress
                )
            )

            # Maintain recent history for ASCII sparkline
            history.append(a_out)
            if len(history) > HISTORY_LEN:
                history.pop(0)
            spark = "".join(spark_char(x) for x in history)

            # 6. Console log (now with history)
            print(
                f"{tick_index:04d}  {time_utc}  "
                f"align={final_align_str}  band={band}  dt_ms={dt_ms:7.1f}  history:{spark}"
            )

            # Stopping condition if max_ticks is set
            if max_ticks > 0 and tick_index >= max_ticks:
                print(f"\nReached max_ticks = {max_ticks}. Stopping.")
                break

            tick_index += 1

            # Sleep to honour tick_sec cadence
            elapsed = time.time() - loop_start
            remaining = tick_sec - elapsed
            if remaining > 0:
                time.sleep(remaining)

    except KeyboardInterrupt:
        print("\nInterrupted by user (Ctrl+C).")

    # Export CSV
    if rows:
        ts = now_utc_stamp_for_filename()
        filename = f"{CSV_BASENAME}_{ts}.csv"
        try:
            with open(filename, "w", newline="", encoding="utf-8") as f_csv:
                writer = csv.writer(f_csv)
                writer.writerow(
                    [
                        "tick_index",
                        "time_utc",
                        "final_align",
                        "band",
                        "stamp",
                        "tick_ms",
                        "dt_ms",
                        "a_stress",
                    ]
                )
                for r in rows:
                    writer.writerow(r)
            print(f"\nExported {len(rows)} rows to {filename}")
        except Exception as e:
            print(f"\nExport failed: {e}")
    else:
        print("\nNo rows recorded; nothing to export.")

# ---------------------------
# CLI entrypoint
# ---------------------------

def main():
    parser = argparse.ArgumentParser(
        description="clockke CLI v2.1 — Shunyaya Symbolic Mathematical Clock (real-entropy)."
    )
    parser.add_argument(
        "--tick-sec",
        type=float,
        default=TICK_SEC_DEFAULT,
        help=f"tick cadence in seconds (default: {TICK_SEC_DEFAULT})",
    )
    parser.add_argument(
        "--ticks",
        type=int,
        default=AUTO_STOP_TICKS_DEFAULT,
        help=(
            "max ticks to run (default: 0 = run until Ctrl+C). "
            "If > 0, stops automatically after this many ticks."
        ),
    )

    args = parser.parse_args()
    tick_sec = max(args.tick_sec, 0.001)  # avoid absurdly small or negative
    max_ticks = max(args.ticks, 0)

    run_clockke_cli(tick_sec, max_ticks)

if __name__ == "__main__":
    main()
