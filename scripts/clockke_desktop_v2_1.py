#!/usr/bin/env python3
"""
clockke_desktop_v2_1.py
Shunyaya Symbolic Mathematical Clock Kernel (SSM-ClockKe)
App identity: clockke

Desktop version v2.1 (same numeric kernel as v2.0):
- Live UI for UTC, final_align, band, stamp
- (U, W) kernel with bounded alignment lane
- Tamper-evident stamp chain
- Export CSV with tick_ms, dt_ms, a_stress
- In-app Verify Chain
- Band-aware colours for stability
- Stability bar for final_align in (-1,+1)
- Alignment stress slider (a_stress)
- Tick cadence selector (tick_ms presets)
- Auto-stop after AUTO_STOP_TICKS ticks
- last dt_ms colour-coded for jitter
- NEW: Real-entropy alignment source based on dt_ms jitter + micro-noise

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
from datetime import datetime, timezone
import tkinter as tk
from tkinter import messagebox

# ---------------------------
# Manifest-style parameters
# ---------------------------

MANIFEST_ID = "CLOCKKE.DEFAULT.V2_1"

EPS_A = 1e-6       # clamp margin for a_raw
EPS_W = 1e-9       # avoid division by zero in W

# Tick cadence can be changed at runtime via selector
TICK_MS = 1000     # default tick cadence (ms)
TICK_OPTIONS = [250, 500, 1000, 2000]

W_DEFAULT = 1.0    # weight per tick

# Exponential decay factor for past evidence
DECAY_W = 0.995    # set to 1.0 to disable decay

# Real-entropy alignment parameters
BASELINE_A = 0.02          # baseline stability
JITTER_GAIN = 0.15         # how strongly jitter moves alignment
FREEZE_MULT = 1.5          # dt_ms > FREEZE_MULT * tick_ms => freeze penalty
FREEZE_PENALTY = 0.05      # penalty applied on serious freeze
NOISE_AMPL = 0.01          # amplitude of random micro-noise in [-NOISE_AMPL,+NOISE_AMPL]

# Alignment stress offset controlled by slider
STRESS_MIN = -0.05
STRESS_MAX =  0.05
STRESS_INIT = 0.0

# Auto-stop safety (change if needed)
AUTO_STOP_TICKS = 600

CSV_BASENAME = "stamps_clockke"

# Band colour map
BAND_COLORS = {
    "A+": "#008000",  # green
    "A":  "#008000",
    "B":  "#CCCC00",  # yellow
    "C":  "#FF7F00",  # orange
    "D":  "#CC0000",  # red
}

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

def source_a_raw(now_epoch, dt_ms, tick_ms, a_stress, rng_obj):
    """
    Real-entropy alignment source for v2.1.

    a_raw is driven by:
    - baseline stability,
    - how far dt_ms deviates from tick_ms,
    - explicit freeze penalties,
    - small random micro-noise,
    - user-controlled a_stress.

    Steps:
    1) jitter = (dt_ms - tick_ms) / max(tick_ms, 1.0)
       - positive jitter => tick is late  -> more negative alignment
       - negative jitter => tick is early -> more positive alignment
    2) jitter_term = -JITTER_GAIN * jitter
    3) if dt_ms > FREEZE_MULT * tick_ms: apply FREEZE_PENALTY
    4) noise_term = NOISE_AMPL * noise_unit, noise_unit in [-NOISE_AMPL,+NOISE_AMPL]
    5) a_raw = BASELINE_A + a_stress + jitter_term + noise_term
    """

    # Fallback for the very first tick when dt_ms may be 0.0
    if dt_ms <= 0.0 or tick_ms <= 0.0:
        jitter = 0.0
    else:
        jitter = (dt_ms - float(tick_ms)) / float(max(tick_ms, 1.0))

    base = BASELINE_A + a_stress

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

def dt_color(dt_ms, tick_ms):
    """
    Colour for last dt_ms:
    - near tick_ms  -> green
    - moderate diff -> orange
    - large diff    -> red
    """
    if tick_ms <= 0:
        return "#000000"
    ratio = abs(dt_ms - tick_ms) / float(tick_ms)
    if ratio < 0.10:
        return "#008000"   # within 10%
    elif ratio < 0.50:
        return "#FF7F00"   # within 50%
    else:
        return "#CC0000"   # very jittery / delayed

# ---------------------------
# State for clockke session
# ---------------------------

U = 0.0
W = 0.0
prev_stamp = ""
tick_index = 1
running = False
rows = []   # (tick_index, time_utc, final_align, band, stamp, tick_ms, dt_ms, a_stress)

last_tick_epoch = None

# ---------------------------
# UI setup
# ---------------------------

root = tk.Tk()
root.title("clockke — Shunyaya Symbolic Mathematical Clock Kernel (SSM-ClockKe)")

lbl_title = tk.Label(
    root,
    text="clockke — symbolic time engine (v2.1, real-entropy)",
    font=("Consolas", 14, "bold")
)
lbl_title.grid(row=0, column=0, columnspan=4, padx=8, pady=8, sticky="w")

lbl_utc = tk.Label(root, text="UTC: -", font=("Consolas", 12))
lbl_utc.grid(row=1, column=0, columnspan=4, padx=8, pady=4, sticky="w")

lbl_align = tk.Label(root, text="final_align: +0.000000000", font=("Consolas", 12))
lbl_align.grid(row=2, column=0, columnspan=4, padx=8, pady=4, sticky="w")

lbl_band = tk.Label(root, text="band: -", font=("Consolas", 12))
lbl_band.grid(row=3, column=0, columnspan=4, padx=8, pady=4, sticky="w")

lbl_stamp = tk.Label(root, text="stamp: -", font=("Consolas", 10))
lbl_stamp.grid(row=4, column=0, columnspan=4, padx=8, pady=4, sticky="w")

# Stability bar
canvas_width = 220
canvas_height = 18
canvas_stability = tk.Canvas(root, width=canvas_width, height=canvas_height,
                             bg="#F0F0F0", highlightthickness=1)
canvas_stability.grid(row=5, column=0, columnspan=4, padx=8, pady=4, sticky="w")

center_x = canvas_width // 2
canvas_stability.create_line(center_x, 2, center_x, canvas_height - 2,
                             fill="#808080")

lbl_stability_caption = tk.Label(
    root,
    text="stability bar (center = 0, right = +1, left = -1)",
    font=("Consolas", 9)
)
lbl_stability_caption.grid(row=6, column=0, columnspan=4, padx=8, pady=2, sticky="w")

# Buttons
btn_start = tk.Button(root, text="Start", width=12)
btn_stop = tk.Button(root, text="Stop", width=12)
btn_export = tk.Button(root, text="Export CSV", width=12)
btn_verify = tk.Button(root, text="Verify Chain", width=12)

btn_start.grid(row=7, column=0, padx=6, pady=8)
btn_stop.grid(row=7, column=1, padx=6, pady=8)
btn_export.grid(row=8, column=0, padx=6, pady=4)
btn_verify.grid(row=8, column=1, padx=6, pady=4)

# Tick cadence selector
lbl_cadence = tk.Label(root, text="tick_ms:", font=("Consolas", 10))
lbl_cadence.grid(row=7, column=2, padx=4, pady=4, sticky="e")

tick_ms_var = tk.IntVar(value=TICK_MS)
opt_tick_ms = tk.OptionMenu(root, tick_ms_var, *TICK_OPTIONS)
opt_tick_ms.config(width=6)
opt_tick_ms.grid(row=7, column=3, padx=4, pady=4, sticky="w")

# Alignment stress slider
lbl_stress = tk.Label(root, text="alignment stress (a_stress):", font=("Consolas", 10))
lbl_stress.grid(row=8, column=2, padx=4, pady=2, sticky="e")

stress_var = tk.DoubleVar(value=STRESS_INIT)
scale_stress = tk.Scale(
    root, from_=STRESS_MIN, to=STRESS_MAX,
    orient=tk.HORIZONTAL, resolution=0.005,
    length=130, variable=stress_var
)
scale_stress.grid(row=8, column=3, padx=4, pady=2, sticky="w")

lbl_stress_value = tk.Label(root, text="a_stress = +0.000", font=("Consolas", 9))
lbl_stress_value.grid(row=9, column=2, columnspan=2, padx=4, pady=2, sticky="e")

lbl_status = tk.Label(root, text="ready.", font=("Consolas", 10))
lbl_status.grid(row=10, column=0, columnspan=4, padx=8, pady=6, sticky="w")

lbl_tick_count = tk.Label(root, text="ticks: 0", font=("Consolas", 9))
lbl_tick_count.grid(row=11, column=0, columnspan=2, padx=8, pady=2, sticky="w")

lbl_dt = tk.Label(root, text="last dt_ms: -", font=("Consolas", 9))
lbl_dt.grid(row=11, column=2, columnspan=2, padx=8, pady=2, sticky="e")

lbl_manifest = tk.Label(
    root,
    text=(
        f"manifest: {MANIFEST_ID} "
        f"| tick_ms={TICK_MS} | auto_stop={AUTO_STOP_TICKS} "
        f"| W_DEFAULT={W_DEFAULT} | DECAY_W={DECAY_W} "
        f"| baseline_a={BASELINE_A:+.3f} | jitter_gain={JITTER_GAIN:.3f}"
    ),
    font=("Consolas", 9)
)
lbl_manifest.grid(row=12, column=0, columnspan=4, padx=8, pady=4, sticky="w")

# ---------------------------
# Logic
# ---------------------------

def reset_engine():
    global U, W, prev_stamp, tick_index, rows, last_tick_epoch
    U = 0.0
    W = 0.0
    prev_stamp = ""
    tick_index = 1
    rows = []
    last_tick_epoch = None
    lbl_dt.config(text="last dt_ms: -", fg="#000000")
    lbl_tick_count.config(text="ticks: 0")
    canvas_stability.delete("bar")
    lbl_status.config(text="ready.")

def update_stress_label():
    a_stress = stress_var.get()
    lbl_stress_value.config(text=f"a_stress = {a_stress:+.3f}")

def draw_stability_bar(a_out, band):
    """
    Draw a bar representing a_out in (-1,+1).
    Center is 0. Extends left for negative, right for positive.
    """
    canvas_stability.delete("bar")

    a_vis = max(-1.0, min(1.0, a_out))
    half_width = canvas_width // 2
    center = half_width
    max_len = half_width - 4

    length = int(a_vis * max_len)
    if length == 0:
        return

    if length > 0:
        x0 = center
        x1 = center + length
    else:
        x0 = center + length
        x1 = center

    y0 = 3
    y1 = canvas_height - 3

    color = BAND_COLORS.get(band, "#000000")
    canvas_stability.create_rectangle(
        x0, y0, x1, y1,
        fill=color, outline=color,
        tags="bar"
    )

def tick():
    global U, W, prev_stamp, tick_index, running, rows, last_tick_epoch, TICK_MS

    if not running:
        return

    # Auto-stop guard
    if tick_index > AUTO_STOP_TICKS:
        running = False
        lbl_status.config(text=f"auto-stop after {AUTO_STOP_TICKS} ticks.")
        messagebox.showinfo("clockke", f"Auto-stop after {AUTO_STOP_TICKS} ticks.")
        return

    # Refresh TICK_MS from selector
    TICK_MS = tick_ms_var.get()

    now_epoch = time.time()
    time_utc = now_utc_iso()

    # dt_ms calculation
    if last_tick_epoch is None:
        # Treat first tick as if it matched the planned cadence
        dt_ms = float(TICK_MS)
    else:
        dt_ms = (now_epoch - last_tick_epoch) * 1000.0
    last_tick_epoch = now_epoch

    # 1. Get alignment source including dt_ms, tick_ms, and stress
    a_stress = stress_var.get()
    a_raw = source_a_raw(now_epoch, dt_ms, TICK_MS, a_stress, rng)

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

    # 5. Record row
    rows.append(
        (
            tick_index,
            time_utc,
            final_align_str,
            band,
            stamp,
            TICK_MS,
            dt_ms,
            a_stress,
        )
    )

    # 6. UI updates
    stamp_tail = f"{stamp[:8]}…{stamp[-8:]}"
    lbl_utc.config(text=f"UTC: {time_utc}")
    lbl_align.config(text=f"final_align: {final_align_str}")
    lbl_band.config(
        text=f"band: {band}",
        fg=BAND_COLORS.get(band, "#000000")
    )
    lbl_stamp.config(text=f"stamp: {stamp_tail}")
    lbl_status.config(text=f"running… ticks={tick_index}")
    lbl_tick_count.config(text=f"ticks: {tick_index}")

    # dt_ms with colour
    lbl_dt.config(
        text=f"last dt_ms: {dt_ms:0.1f}",
        fg=dt_color(dt_ms, TICK_MS)
    )

    draw_stability_bar(a_out, band)
    update_stress_label()

    tick_index += 1
    root.after(TICK_MS, tick)

def on_start():
    global running
    if running:
        return
    reset_engine()
    running = True
    lbl_status.config(text="running…")
    tick()

def on_stop():
    global running
    running = False
    lbl_status.config(text="stopped.")

def on_export():
    if not rows:
        messagebox.showinfo("clockke", "No ticks recorded yet.")
        return

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
        lbl_status.config(text=f"exported {filename}")
        messagebox.showinfo("clockke", f"Exported {filename}")
    except Exception as e:
        lbl_status.config(text="export failed.")
        messagebox.showerror("clockke", f"Export failed: {e}")

def verify_rows():
    """
    Verify in-memory rows using:
    payload_str := 'time_utc|final_align|band'
    stamp_k := sha256(prev_stamp || sha256(payload_bytes) || time_utc)
    """
    if not rows:
        return True, 0, "", ""

    prev = ""
    for r in rows:
        tick_idx, time_utc, final_align_str, band, stamp = r[:5]
        payload_str = f"{time_utc}|{final_align_str}|{band}"
        expected = make_stamp(prev, payload_str, time_utc)
        if expected != stamp:
            return False, tick_idx, stamp, expected
        prev = stamp

    return True, len(rows), "", ""

def on_verify():
    ok, val, stored, expected = verify_rows()
    if ok:
        msg = f"ALL CHECKS PASSED\nTicks verified: {val}"
        lbl_status.config(text="ALL CHECKS PASSED.")
        messagebox.showinfo("clockke", msg)
    else:
        lbl_status.config(text="verification FAILED.")
        message = (
            "VERIFICATION FAILED\n"
            f"First mismatch at tick_index = {val}\n"
            f"Stored stamp:   {stored}\n"
            f"Expected stamp: {expected}"
        )
        messagebox.showerror("clockke", message)

# ---------------------------
# Wire buttons & start
# ---------------------------

btn_start.config(command=on_start)
btn_stop.config(command=on_stop)
btn_export.config(command=on_export)
btn_verify.config(command=on_verify)

update_stress_label()
lbl_status.config(text="ready. press Start to begin symbolic time.")

root.mainloop()
