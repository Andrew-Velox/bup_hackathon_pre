from typing import List
from pydantic import BaseModel, Field, field_validator, model_validator

class HourEntry(BaseModel):
    hour: int = Field(..., ge=0, le=23, description="Unique hour integer from 0 to 23")
    demand_kwh: float = Field(..., ge=0.0, description="Campus demand that must be supplied in this hour")
    solar_kwh: float = Field(..., ge=0.0, description="Base solar energy available before adjustments")
    tariff_bdt_per_kwh: float = Field(..., ge=0.0, description="Grid electricity price for this hour")

class BatteryConfig(BaseModel):
    capacity_kwh: float = Field(..., gt=0.0, description="Maximum energy the battery can store")
    initial_energy_kwh: float = Field(..., ge=0.0, description="Battery energy at the start of hour 0")
    minimum_energy_kwh: float = Field(..., ge=0.0, description="Base reserve level the battery must never go below")
    max_charge_kwh_per_hour: float = Field(..., ge=0.0, description="Maximum energy that may be added in one hour")
    max_discharge_kwh_per_hour: float = Field(..., ge=0.0, description="Maximum energy that may be removed in one hour")

    @model_validator(mode="after")
    def validate_battery(self) -> "BatteryConfig":
        if self.initial_energy_kwh > self.capacity_kwh:
            raise ValueError("initial_energy_kwh cannot exceed capacity_kwh")
        if self.minimum_energy_kwh > self.capacity_kwh:
            raise ValueError("minimum_energy_kwh cannot exceed capacity_kwh")
        if self.initial_energy_kwh < self.minimum_energy_kwh:
            raise ValueError("initial_energy_kwh cannot be less than minimum_energy_kwh")
        return self

class OptimizationRequest(BaseModel):
    scenario_id: str = Field(..., min_length=1, description="Unique synthetic scenario identifier")
    operator_notes: List[str] = Field(..., min_length=1, max_length=3, description="Natural-language operator notes (1 to 3)")
    hours: List[HourEntry] = Field(..., description="Hourly energy parameters for exactly 24 hours (0 to 23)")
    battery: BatteryConfig = Field(..., description="Battery configuration parameters")

    @field_validator("operator_notes")
    @classmethod
    def validate_notes(cls, notes: List[str]) -> List[str]:
        for idx, note in enumerate(notes):
            if not note or not note.strip():
                raise ValueError(f"operator_notes[{idx}] cannot be empty")
        return notes

    @field_validator("hours")
    @classmethod
    def validate_hours(cls, hours: List[HourEntry]) -> List[HourEntry]:
        if len(hours) != 24:
            raise ValueError(f"hours array must contain exactly 24 entries, got {len(hours)}")
        
        seen_hours = set()
        for entry in hours:
            if entry.hour in seen_hours:
                raise ValueError(f"Duplicate hour {entry.hour} in hours array")
            seen_hours.add(entry.hour)
            
        if seen_hours != set(range(24)):
            missing = sorted(list(set(range(24)) - seen_hours))
            raise ValueError(f"hours array is missing hours: {missing}")
            
        # Ensure hours are sorted 0..23
        return sorted(hours, key=lambda x: x.hour)
