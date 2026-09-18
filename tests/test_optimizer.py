import pytest
from fastapi.testclient import TestClient
from main import app
from utils.guardrails import rule_based_extract_directive, parse_time_window

client = TestClient(app)

def create_sample_payload():
    # 24-hour synthetic profile
    hours = []
    for h in range(24):
        # Peak solar around noon (hours 10-15)
        solar = 0.0
        if 6 <= h <= 17:
            solar = max(0.0, 350.0 - abs(h - 12) * 50.0)
            
        # Demand profile (higher during morning/afternoon 8-18)
        demand = 180.0 if (h < 8 or h > 20) else 350.0
        
        # Tariff: cheaper at night, peak during evening
        tariff = 6.5 if (h < 6 or h > 22) else (12.0 if 17 <= h <= 22 else 8.5)

        hours.append({
            "hour": h,
            "demand_kwh": demand,
            "solar_kwh": solar,
            "tariff_bdt_per_kwh": tariff
        })

    battery = {
        "capacity_kwh": 500.0,
        "initial_energy_kwh": 200.0,
        "minimum_energy_kwh": 50.0,
        "max_charge_kwh_per_hour": 100.0,
        "max_discharge_kwh_per_hour": 100.0
    }

    notes = [
        "Solar output will drop to about 20% from 1 PM to 3 PM.",
        "Do not charge the battery between 2 PM and 4 PM.",
        "The cafeteria menu changes tomorrow."
    ]

    return {
        "scenario_id": "GRID-SCENARIO-2026-01",
        "operator_notes": notes,
        "hours": hours,
        "battery": battery
    }

def test_time_window_parser():
    assert parse_time_window("from 1 PM to 3 PM") == [13, 14]
    assert parse_time_window("between 6 PM and 9 PM") == [18, 19, 20]
    assert parse_time_window("14:00 to 16:00") == [14, 15]

def test_directive_extraction_guardrails():
    # Note 1: Solar reduction
    d0 = rule_based_extract_directive("Solar output will drop to about 20% from 1 PM to 3 PM.", 0).model_dump()
    assert d0["applies"] is True
    assert d0["directive_type"] == "solar_reduction"
    assert d0["structured_adjustment"]["hours"] == [13, 14]
    assert abs(d0["structured_adjustment"]["factor"] - 0.2) < 1e-4

    # Note 2: No charge window
    d1 = rule_based_extract_directive("Do not charge the battery between 2 PM and 4 PM.", 1).model_dump()
    assert d1["applies"] is True
    assert d1["directive_type"] == "no_charge_window"
    assert d1["structured_adjustment"]["hours"] == [14, 15]

    # Note 3: Irrelevant note (no_op)
    d2 = rule_based_extract_directive("The cafeteria menu changes tomorrow.", 2).model_dump()
    assert d2["applies"] is False
    assert d2["directive_type"] == "no_op"
    assert d2["structured_adjustment"] is None

def test_optimize_energy_full_pipeline():
    payload = create_sample_payload()
    response = client.post("/optimize-energy", json=payload)
    
    assert response.status_code == 200
    data = response.json()

    # Verify top-level structure
    assert data["scenario_id"] == "GRID-SCENARIO-2026-01"
    assert len(data["directive_interpretation"]) == 3
    assert len(data["hourly_plan"]) == 24
    assert data["total_grid_kwh"] > 0
    assert data["total_cost_bdt"] > 0
    assert data["peak_grid_kwh"] > 0
    assert len(data["plan_summary"]) > 0

    # Verify directive interpretation
    d_interp = data["directive_interpretation"]
    assert d_interp[0]["note_index"] == 0
    assert d_interp[0]["directive_type"] == "solar_reduction"
    assert d_interp[0]["structured_adjustment"]["hours"] == [13, 14]

    assert d_interp[1]["note_index"] == 1
    assert d_interp[1]["directive_type"] == "no_charge_window"
    assert d_interp[1]["structured_adjustment"]["hours"] == [14, 15]

    assert d_interp[2]["note_index"] == 2
    assert d_interp[2]["directive_type"] == "no_op"
    assert d_interp[2]["applies"] is False
    assert d_interp[2]["structured_adjustment"] is None

    # Replay hourly plan and verify all physical and accounting constraints
    plan = data["hourly_plan"]
    hours_req = payload["hours"]
    battery_req = payload["battery"]
    current_energy = battery_req["initial_energy_kwh"]

    for entry in plan:
        h = entry["hour"]
        grid = entry["grid_kwh"]
        solar_used = entry["solar_used_kwh"]
        action = entry["battery_action"]
        b_kwh = entry["battery_kwh"]
        e_after = entry["battery_energy_after_kwh"]

        # Action constraints
        assert action in ("charge", "discharge", "idle")
        if action == "idle":
            assert b_kwh == 0.0
            next_energy = current_energy
        elif action == "charge":
            assert b_kwh > 0.0
            assert b_kwh <= battery_req["max_charge_kwh_per_hour"] + 0.01
            # Check no_charge_window for hours 14, 15
            assert h not in [14, 15]
            next_energy = current_energy + b_kwh
        else: # discharge
            assert b_kwh > 0.0
            assert b_kwh <= battery_req["max_discharge_kwh_per_hour"] + 0.01
            next_energy = current_energy - b_kwh

        # Battery state transition
        assert abs(e_after - next_energy) <= 0.05
        current_energy = e_after

        # Battery capacity and reserve bounds
        assert battery_req["minimum_energy_kwh"] - 0.01 <= e_after <= battery_req["capacity_kwh"] + 0.01

        # Solar constraint
        orig_solar = hours_req[h]["solar_kwh"]
        eff_solar = orig_solar * 0.2 if h in [13, 14] else orig_solar
        assert solar_used <= eff_solar + 0.01

        # Energy balance
        demand = hours_req[h]["demand_kwh"]
        charge_amt = b_kwh if action == "charge" else 0.0
        discharge_amt = b_kwh if action == "discharge" else 0.0
        assert abs((grid + solar_used + discharge_amt) - (demand + charge_amt)) <= 0.05

    # Verify end-of-day battery neutrality
    assert abs(plan[23]["battery_energy_after_kwh"] - battery_req["initial_energy_kwh"]) <= 0.05

    # Verify aggregate metrics match hourly_plan recalculation
    recalc_grid = round(sum(e["grid_kwh"] for e in plan), 2)
    recalc_cost = round(sum(e["grid_kwh"] * hours_req[e["hour"]]["tariff_bdt_per_kwh"] for e in plan), 2)
    recalc_peak = round(max(e["grid_kwh"] for e in plan), 2)

    assert abs(data["total_grid_kwh"] - recalc_grid) <= 0.05
    assert abs(data["total_cost_bdt"] - recalc_cost) <= 0.05
    assert abs(data["peak_grid_kwh"] - recalc_peak) <= 0.05

def test_optimize_energy_invalid_request():
    # Test with fewer than 24 hours
    invalid_payload = create_sample_payload()
    invalid_payload["hours"] = invalid_payload["hours"][:10]

    response = client.post("/optimize-energy", json=invalid_payload)
    assert response.status_code == 400

def test_paraphrased_solar_reduction_notes():
    # Paraphrased variations from problem statement Section 11.4
    variations = [
        "PV production will drop to about 20% between 13:00 and 15:00.",
        "Panel washing from one until three will leave roughly one-fifth of normal solar output.",
        "Expect an 80% reduction in rooftop solar during the 1-3 PM maintenance window."
    ]
    for idx, var_note in enumerate(variations):
        d = rule_based_extract_directive(var_note, idx).model_dump()
        assert d["applies"] is True
        assert d["directive_type"] == "solar_reduction"
        assert d["structured_adjustment"]["hours"] == [13, 14]
        assert abs(d["structured_adjustment"]["factor"] - 0.2) <= 0.01

def test_other_directives():
    # Minimum battery reserve
    d_res = rule_based_extract_directive("Keep at least 120 kWh in reserve from 6 PM until 9 PM.", 0).model_dump()
    assert d_res["applies"] is True
    assert d_res["directive_type"] == "minimum_battery_reserve"
    assert d_res["structured_adjustment"]["hours"] == [18, 19, 20]
    assert d_res["structured_adjustment"]["minimum_energy_kwh"] == 120.0

    # Max grid window
    d_grid = rule_based_extract_directive("Grid import cannot exceed 150 kWh between 5 PM and 8 PM.", 1).model_dump()
    assert d_grid["applies"] is True
    assert d_grid["directive_type"] == "max_grid_window"
    assert d_grid["structured_adjustment"]["hours"] == [17, 18, 19]
    assert d_grid["structured_adjustment"]["max_grid_kwh"] == 150.0

    # No discharge window
    d_dis = rule_based_extract_directive("Do not discharge the battery between 10 AM and 12 PM.", 2).model_dump()
    assert d_dis["applies"] is True
    assert d_dis["directive_type"] == "no_discharge_window"
    assert d_dis["structured_adjustment"]["hours"] == [10, 11]

