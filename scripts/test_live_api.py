#!/usr/bin/env python3
"""
GridWise Live API Test Client
Sends direct HTTP requests to the running FastAPI server,
validates response schemas, evaluates mathematical and physical constraints,
and displays human-readable summary tables.
"""

import os
import sys
import json
import time
import urllib.request
import urllib.error
from pathlib import Path

API_BASE_URL = os.getenv("API_BASE_URL", "https://bup-hackathon-pre.onrender.com")
PAYLOAD_DIR = Path(__file__).resolve().parent.parent / "test_payloads"

# ANSI Colors
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def check_health(timeout: float = 3.0) -> bool:
    url = f"{API_BASE_URL}/health"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as response:
            if response.status == 200:
                data = json.loads(response.read().decode("utf-8"))
                return data.get("status") == "ok"
    except Exception:
        return False
    return False


def call_optimize_energy(payload: dict, timeout: float = 30.0) -> tuple[dict | None, float, str | None]:
    url = f"{API_BASE_URL}/optimize-energy"
    headers = {"Content-Type": "application/json"}
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    
    start_time = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            data = json.loads(response.read().decode("utf-8"))
            return data, latency_ms, None
    except urllib.error.HTTPError as e:
        latency_ms = (time.perf_counter() - start_time) * 1000.0
        err_msg = f"HTTP {e.code}: {e.read().decode('utf-8')}"
        return None, latency_ms, err_msg
    except Exception as e:
        latency_ms = (time.perf_counter() - start_time) * 1000.0
        return None, latency_ms, str(e)


def validate_plan_physics(payload: dict, response: dict) -> list[str]:
    """Verify 24h physics, battery limits, energy balance, and neutrality."""
    issues = []
    hourly_plan = response.get("hourly_plan", [])
    if len(hourly_plan) != 24:
        issues.append(f"Expected 24 hours in hourly_plan, got {len(hourly_plan)}")
        return issues

    battery_cfg = payload["battery"]
    cap = battery_cfg["capacity_kwh"]
    max_c = battery_cfg["max_charge_kwh_per_hour"]
    max_d = battery_cfg["max_discharge_kwh_per_hour"]
    min_e = battery_cfg["minimum_energy_kwh"]
    init_soc = battery_cfg["initial_energy_kwh"]

    prev_soc = init_soc
    hours_req = {h["hour"]: h for h in payload["hours"]}

    for h_data in hourly_plan:
        h = h_data["hour"]
        req_h = hours_req[h]
        demand = req_h["demand_kwh"]
        solar_base = req_h["solar_kwh"]

        grid = h_data["grid_kwh"]
        solar_used = h_data["solar_used_kwh"]
        action = h_data["battery_action"]
        b_kwh = h_data["battery_kwh"]
        soc = h_data["battery_energy_after_kwh"]

        # 1. Action value sanity
        if action == "idle" and b_kwh > 0.01:
            issues.append(f"H{h:02d} Idle action but battery_kwh is {b_kwh:.2f}")

        # 2. Rate limits
        if action == "charge" and b_kwh > max_c + 1e-3:
            issues.append(f"H{h:02d} Charge {b_kwh:.2f} exceeds max {max_c:.2f}")
        if action == "discharge" and b_kwh > max_d + 1e-3:
            issues.append(f"H{h:02d} Discharge {b_kwh:.2f} exceeds max {max_d:.2f}")

        # 3. Energy balance: grid + solar_used + discharge == demand + charge
        discharge_val = b_kwh if action == "discharge" else 0.0
        charge_val = b_kwh if action == "charge" else 0.0
        supplied = round(grid + solar_used + discharge_val, 2)
        demanded = round(demand + charge_val, 2)
        if abs(supplied - demanded) > 0.05:
            issues.append(f"H{h:02d} Energy imbalance: supplied {supplied:.2f} != demanded {demanded:.2f}")

        # 4. Solar used cannot exceed base solar
        if solar_used > solar_base + 1e-3:
            issues.append(f"H{h:02d} Solar used {solar_used:.2f} exceeds available solar {solar_base:.2f}")

        # 5. SoC bounds & continuity
        if soc < min_e - 0.05 or soc > cap + 0.05:
            issues.append(f"H{h:02d} Battery SoC {soc:.2f} out of bounds [{min_e}, {cap}]")

        expected_soc = round(prev_soc + charge_val - discharge_val, 2)
        if abs(soc - expected_soc) > 0.05:
            issues.append(f"H{h:02d} SoC evolution mismatch: reported {soc:.2f} vs expected {expected_soc:.2f}")

        prev_soc = soc

    # 6. End-of-day neutrality
    final_soc = hourly_plan[-1]["battery_energy_after_kwh"]
    if abs(final_soc - init_soc) > 0.05:
        issues.append(f"End-of-day SoC {final_soc:.2f} != initial SoC {init_soc:.2f}")

    return issues


def validate_directive_compliance(payload: dict, response: dict) -> list[str]:
    """Verify if active directives were physically respected in the hourly plan."""
    issues = []
    interpretations = response.get("directive_interpretation", [])
    hourly = {h["hour"]: h for h in response.get("hourly_plan", [])}
    req_hours = {h["hour"]: h for h in payload.get("hours", [])}

    for interp in interpretations:
        if not interp.get("applies"):
            continue
        d_type = interp.get("directive_type")
        adj = interp.get("structured_adjustment") or {}
        hours = adj.get("hours", [])

        if d_type == "minimum_battery_reserve":
            threshold = adj.get("minimum_energy_kwh", 0.0)
            for h in hours:
                if h in hourly and hourly[h]["battery_energy_after_kwh"] < threshold - 0.05:
                    issues.append(f"H{h:02d} SoC {hourly[h]['battery_energy_after_kwh']:.2f} < reserve {threshold:.2f}")

        elif d_type == "no_charge_window":
            for h in hours:
                if h in hourly and hourly[h]["battery_action"] == "charge":
                    issues.append(f"H{h:02d} Battery charged during no_charge_window")

        elif d_type == "no_discharge_window":
            for h in hours:
                if h in hourly and hourly[h]["battery_action"] == "discharge":
                    issues.append(f"H{h:02d} Battery discharged during no_discharge_window")

        elif d_type == "solar_reduction":
            factor = adj.get("factor", 1.0)
            for h in hours:
                if h in hourly and h in req_hours:
                    max_allowed_solar = round(req_hours[h]["solar_kwh"] * factor, 2)
                    if hourly[h]["solar_used_kwh"] > max_allowed_solar + 0.05:
                        issues.append(f"H{h:02d} Solar used {hourly[h]['solar_used_kwh']:.2f} > capped {max_allowed_solar:.2f}")

    return issues


def run_tests():
    print(f"\n{BOLD}{CYAN}======================================================================{RESET}")
    print(f"{BOLD}{CYAN}           GridWise Autonomous Energy Optimization API Test           {RESET}")
    print(f"{BOLD}{CYAN}======================================================================{RESET}\n")
    print(f"Target API Server: {BOLD}{API_BASE_URL}{RESET}")

    # 1. Health Check
    print("Verifying server health endpoint (/health)...", end=" ", flush=True)
    if not check_health():
        print(f"{RED}[FAILED]{RESET}")
        print(f"{RED}Server at {API_BASE_URL} is not responding or unhealthy.{RESET}")
        sys.exit(1)
    print(f"{GREEN}[ONLINE - Healthy]{RESET}\n")

    # 2. Iterate Test Cases
    payload_files = sorted(PAYLOAD_DIR.glob("case*.json"))
    if not payload_files:
        print(f"{RED}No payload files found in {PAYLOAD_DIR}{RESET}")
        sys.exit(1)

    all_passed = True

    for p_file in payload_files:
        with open(p_file, "r", encoding="utf-8") as f:
            payload = json.load(f)

        scenario_id = payload.get("scenario_id", p_file.stem)
        notes = payload.get("operator_notes", [])
        print(f"{BOLD}----------------------------------------------------------------------{RESET}")
        print(f"{BOLD}Scenario:{RESET} {YELLOW}{scenario_id}{RESET} ({p_file.name})")
        print(f"{BOLD}Operator Notes:{RESET}")
        for idx, note in enumerate(notes):
            print(f"  [{idx}] \"{note}\"")

        res, latency_ms, err = call_optimize_energy(payload)
        if err:
            print(f"\n{RED}ERROR: Direct API call failed: {err}{RESET} ({latency_ms:.1f} ms)")
            all_passed = False
            continue

        interps = res.get("directive_interpretation", [])
        physics_issues = validate_plan_physics(payload, res)
        compliance_issues = validate_directive_compliance(payload, res)

        physics_status = f"{GREEN}PASS{RESET}" if not physics_issues else f"{RED}FAIL ({len(physics_issues)} violations){RESET}"
        compliance_status = f"{GREEN}PASS{RESET}" if not compliance_issues else f"{RED}FAIL ({len(compliance_issues)} violations){RESET}"

        print(f"\n{BOLD}API Response Metrics:{RESET}")
        print(f"  Roundtrip Latency:   {BOLD}{latency_ms:.1f} ms{RESET}")
        print(f"  Total Electricity:   {BOLD}{res.get('total_grid_kwh'):.2f} kWh{RESET}")
        print(f"  Total Cost:          {BOLD}{res.get('total_cost_bdt'):.2f} BDT{RESET}")
        print(f"  Peak Grid Import:    {BOLD}{res.get('peak_grid_kwh'):.2f} kW{RESET}")
        print(f"  Plan Summary:        \"{res.get('plan_summary')}\"")

        print(f"\n{BOLD}Directive Interpretations:{RESET}")
        for interp in interps:
            idx = interp.get("note_index")
            applies = interp.get("applies")
            d_type = interp.get("directive_type")
            adj = interp.get("structured_adjustment")
            applies_str = f"{GREEN}Active{RESET}" if applies else f"{YELLOW}No-Op{RESET}"
            print(f"  Note [{idx}]: [{applies_str}] type={BOLD}{d_type}{RESET} adj={adj}")

        print(f"\n{BOLD}Verification Checks:{RESET}")
        print(f"  Physical Laws (Conservation & Battery Bounds): [{physics_status}]")
        print(f"  Directive Constraint Enforcement:             [{compliance_status}]")

        if physics_issues:
            for issue in physics_issues[:3]:
                print(f"    {RED}! {issue}{RESET}")
            all_passed = False

        if compliance_issues:
            for issue in compliance_issues[:3]:
                print(f"    {RED}! {issue}{RESET}")
            all_passed = False

        print()

    print(f"{BOLD}----------------------------------------------------------------------{RESET}")
    if all_passed:
        print(f"{GREEN}{BOLD}✓ ALL 4 LIVE API SCENARIOS EXECUTED AND PASSED VERIFICATION!{RESET}\n")
    else:
        print(f"{RED}{BOLD}✗ ONE OR MORE LIVE API SCENARIOS FAILED VERIFICATION.{RESET}\n")
        sys.exit(1)


if __name__ == "__main__":
    run_tests()
