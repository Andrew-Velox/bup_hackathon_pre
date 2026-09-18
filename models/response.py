from typing import List, Optional, Literal, Dict, Any, Union
from pydantic import BaseModel, Field

DirectiveType = Literal[
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op"
]

BatteryAction = Literal["charge", "discharge", "idle"]

class SolarReductionAdjustment(BaseModel):
    hours: List[int] = Field(..., description="Unique hours 0-23 in ascending order")
    factor: float = Field(..., ge=0.0, le=1.0, description="Usable solar fraction remaining (0.0 to 1.0)")

class MinimumBatteryReserveAdjustment(BaseModel):
    hours: List[int] = Field(..., description="Unique hours 0-23 in ascending order")
    minimum_energy_kwh: float = Field(..., ge=0.0, description="Minimum reserve energy threshold in kWh")

class WindowAdjustment(BaseModel):
    hours: List[int] = Field(..., description="Unique hours 0-23 in ascending order")

class MaxGridWindowAdjustment(BaseModel):
    hours: List[int] = Field(..., description="Unique hours 0-23 in ascending order")
    max_grid_kwh: float = Field(..., ge=0.0, description="Maximum grid import limit in kWh")

StructuredAdjustmentType = Optional[Union[
    SolarReductionAdjustment,
    MinimumBatteryReserveAdjustment,
    WindowAdjustment,
    MaxGridWindowAdjustment,
    Dict[str, Any]
]]

class DirectiveInterpretationEntry(BaseModel):
    note_index: int = Field(..., ge=0, description="Zero-based index of corresponding operator note")
    applies: bool = Field(..., description="true for active directives, false only for no_op")
    directive_type: DirectiveType = Field(..., description="Supported directive type")
    structured_adjustment: StructuredAdjustmentType = Field(None, description="Structured parameters, null for no_op")
    explanation: str = Field(..., description="Short explanation of the interpretation")

class HourlyPlanEntry(BaseModel):
    hour: int = Field(..., ge=0, le=23, description="Hour of the day 0 to 23")
    grid_kwh: float = Field(..., ge=0.0, description="Non-negative grid energy purchased in this hour")
    solar_used_kwh: float = Field(..., ge=0.0, description="Solar energy consumed in this hour")
    battery_action: BatteryAction = Field(..., description="'charge', 'discharge', or 'idle'")
    battery_kwh: float = Field(..., ge=0.0, description="Energy transferred; 0.0 if idle")
    battery_energy_after_kwh: float = Field(..., ge=0.0, description="Battery energy state after this hour")

class OptimizationResponse(BaseModel):
    scenario_id: str = Field(..., description="Echo of request scenario_id")
    directive_interpretation: List[DirectiveInterpretationEntry] = Field(..., description="One entry per note in note_index order")
    hourly_plan: List[HourlyPlanEntry] = Field(..., description="24 hourly schedule entries")
    total_grid_kwh: float = Field(..., ge=0.0, description="Sum of grid_kwh across all 24 hours")
    total_cost_bdt: float = Field(..., ge=0.0, description="Total grid electricity cost in BDT")
    peak_grid_kwh: float = Field(..., ge=0.0, description="Maximum hourly grid_kwh")
    plan_summary: str = Field(..., description="Human-readable explanation of the strategy")
