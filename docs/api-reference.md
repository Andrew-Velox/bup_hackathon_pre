# API Reference & Specification

## Endpoints

| Method | Path | Description | Status Codes |
| :--- | :--- | :--- | :--- |
| `GET` | `/health` | Judge harness readiness check | `200` |
| `POST` | `/optimize-energy` | Main optimization & directive interpretation | `200`, `400`, `500` |

---

## 1. `GET /health`

### Response (200 OK)
```json
{
  "status": "ok"
}
```

---

## 2. `POST /optimize-energy`

### Request Schema

```typescript
interface OptimizationRequest {
  scenario_id: string;
  operator_notes: string[]; // 1 to 3 items
  hours: HourEntry[];       // Exactly 24 entries (0 to 23)
  battery: BatteryConfig;
}

interface HourEntry {
  hour: number;               // 0 to 23
  demand_kwh: number;         // >= 0.0
  solar_kwh: number;          // >= 0.0
  tariff_bdt_per_kwh: number; // >= 0.0
}

interface BatteryConfig {
  capacity_kwh: number;            // > 0.0
  initial_energy_kwh: number;      // >= 0.0, <= capacity_kwh
  minimum_energy_kwh: number;      // >= 0.0, <= initial_energy_kwh
  max_charge_kwh_per_hour: number; // >= 0.0
  max_discharge_kwh_per_hour: number; // >= 0.0
}
```

### Response Schema

```typescript
interface OptimizationResponse {
  scenario_id: string;
  directive_interpretation: DirectiveInterpretationEntry[];
  hourly_plan: HourlyPlanEntry[]; // Exactly 24 entries
  total_grid_kwh: number;
  total_cost_bdt: number;
  peak_grid_kwh: number;
  plan_summary: string;
}

interface DirectiveInterpretationEntry {
  note_index: number;
  applies: boolean;
  directive_type: "solar_reduction" | "minimum_battery_reserve" | "no_charge_window" | "no_discharge_window" | "max_grid_window" | "no_op";
  structured_adjustment: Record<string, any> | null;
  explanation: string;
}

interface HourlyPlanEntry {
  hour: number;
  grid_kwh: number;
  solar_used_kwh: number;
  battery_action: "charge" | "discharge" | "idle";
  battery_kwh: number;
  battery_energy_after_kwh: number;
}
```

---

## 3. Supported Directives and Structured Adjustments

| `directive_type` | `applies` | `structured_adjustment` Format | Example Meaning |
| :--- | :--- | :--- | :--- |
| `solar_reduction` | `true` | `{"hours": [13, 14], "factor": 0.2}` | Solar dropped to 20% remaining from 1 PM to 3 PM. |
| `minimum_battery_reserve` | `true` | `{"hours": [18, 19, 20], "minimum_energy_kwh": 120.0}` | Maintain at least 120 kWh in reserve from 6 PM to 9 PM. |
| `no_charge_window` | `true` | `{"hours": [14, 15]}` | Battery charging disallowed between 2 PM and 4 PM. |
| `no_discharge_window` | `true` | `{"hours": [10, 11]}` | Battery discharging disallowed between 10 AM and 12 PM. |
| `max_grid_window` | `true` | `{"hours": [17, 18, 19], "max_grid_kwh": 150.0}` | Grid imports cannot exceed 150 kWh from 5 PM to 8 PM. |
| `no_op` | `false` | `null` | Distractor note with no impact on the energy schedule. |
