#!/usr/bin/env python3
"""
clockke_verify.py
Verifier for stamps_clockke.csv produced by clockke_run.py

Usage:
    python clockke_verify.py
or:
    python clockke_verify.py stamps_clockke.csv
"""

import sys
import csv
import hashlib

DEFAULT_CSV = "stamps_clockke.csv"

def make_stamp(prev_stamp, payload_str, time_utc_str):
    """
    Same chain rule as in clockke_run.py:
    stamp_k := sha256(prev_stamp || sha256(payload_bytes) || time_utc_str)
    """
    payload_bytes = payload_str.encode("utf-8")
    h_payload = hashlib.sha256(payload_bytes).hexdigest().encode("utf-8")
    prev_bytes = (prev_stamp or "").encode("utf-8")
    time_bytes = time_utc_str.encode("utf-8")
    raw = prev_bytes + h_payload + time_bytes
    return hashlib.sha256(raw).hexdigest()

def verify_file(csv_path):
    print("")
    print("clockke stamp verifier")
    print(f"Verifying file: {csv_path}")
    print("")

    with open(csv_path, "r", newline="", encoding="utf-8") as f_csv:
        reader = csv.DictReader(f_csv)
        prev_stamp = ""
        count = 0

        for row in reader:
            tick_index_str = row["tick_index"]
            time_utc = row["time_utc"]
            final_align_str = row["final_align"]
            band = row["band"]
            stored_stamp = row["stamp"]

            payload_str = f"{time_utc}|{final_align_str}|{band}"
            expected_stamp = make_stamp(prev_stamp, payload_str, time_utc)

            if expected_stamp != stored_stamp:
                print("VERIFICATION FAILED")
                print(f"First mismatch at tick_index = {tick_index_str}")
                print(f"Stored stamp:   {stored_stamp}")
                print(f"Expected stamp: {expected_stamp}")
                print("")
                return

            prev_stamp = stored_stamp
            count += 1

    print("ALL CHECKS PASSED")
    print(f"Total ticks verified: {count}")
    print("")

def main():
    if len(sys.argv) > 1:
        csv_path = sys.argv[1]
    else:
        csv_path = DEFAULT_CSV

    verify_file(csv_path)

if __name__ == "__main__":
    main()
