import re
from typing import List, Dict, Any, Optional, Tuple
from models.response import (
    DirectiveInterpretationEntry,
    HourlyPlanEntry,
    SolarReductionAdjustment,
    MinimumBatteryReserveAdjustment,
    WindowAdjustment,
    MaxGridWindowAdjustment,
)
from models.request import OptimizationRequest

ALLOWED_DIRECTIVE_TYPES = {
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op"
}

def sanitize_hours(raw_hours: Any) -> List[int]:
    """Sanitize and sort hours list ensuring unique integers in 0..23."""
    if not isinstance(raw_hours, list):
        return []
    valid_hours = set()
    for h in raw_hours:
        try:
            h_int = int(h)
            if 0 <= h_int <= 23:
                valid_hours.add(h_int)
        except (ValueError, TypeError):
            continue
    return sorted(list(valid_hours))

def parse_time_window(text: str) -> List[int]:
    """
    Extract whole-hour intervals [start, end) from natural language text.
    E.g., '1 PM to 3 PM' -> [13, 14], '14:00 to 16:00' -> [14, 15], 'between 6 PM and 9 PM' -> [18, 19, 20].
    """
    t = text.lower()
    
    # Word numbers mapping
    word_to_num = {
        "twelve": 12, "one": 1, "two": 2, "three": 3, "four": 4,
        "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
        "ten": 10, "eleven": 11, "noon": 12, "midnight": 0
    }
    for word, num in word_to_num.items():
        t = re.sub(rf"\b{word}\b", str(num), t)

    # Patterns for 12-hour or 24-hour intervals
    # E.g. 'from 1 until 3', '1-3 pm', '13:00 to 15:00', 'between 6 pm and 9 pm'
    match = re.search(r'(?:from|between|during(?: the)?)?\s*(\d{1,2})(?::00)?\s*(am|pm)?\s*(?:to|-|until|and)\s*(\d{1,2})(?::00)?\s*(am|pm)?', t)
    if match:
        h1 = int(match.group(1))
        m1 = match.group(2)
        h2 = int(match.group(3))
        m2 = match.group(4)

        if not m1 and m2:
            m1 = m2
        elif not m2 and m1:
            m2 = m1
        elif not m1 and not m2:
            # Neither has am/pm
            if h1 <= 12 and h2 <= 12:
                # Operational hours <= 7 in afternoon context e.g. 1 until 3
                if h1 <= 7:
                    m1 = m2 = "pm"
                else:
                    m1 = m2 = "am"

        def to_24(hour: int, meridiem: Optional[str]) -> int:
            if meridiem == 'pm' and hour < 12:
                return hour + 12
            if meridiem == 'am' and hour == 12:
                return 0
            return hour

        start_24 = to_24(h1, m1)
        end_24 = to_24(h2, m2)

        if 0 <= start_24 <= 23 and 0 <= end_24 <= 24 and start_24 < end_24:
            return list(range(start_24, end_24))

    # Pattern for 24-hour military format: 13:00 to 15:00
    match_24 = re.search(r'(\d{1,2}):00\s*(?:to|-|until|and)\s*(\d{1,2}):00', t)
    if match_24:
        start_24 = int(match_24.group(1))
        end_24 = int(match_24.group(2))
        if 0 <= start_24 <= 23 and 0 <= end_24 <= 24 and start_24 < end_24:
            return list(range(start_24, end_24))

    # Single hour mentions: at 14:00 or at 2 PM
    match_single = re.search(r'(?:at|hour)\s*(\d{1,2})(?::00)?\s*(am|pm)?', t)
    if match_single:
        h = int(match_single.group(1))
        m = match_single.group(2)
        if m == 'pm' and h < 12:
            h += 12
        elif m == 'am' and h == 12:
            h = 0
        if 0 <= h <= 23:
            return [h]

    return []

def rule_based_extract_directive(note: str, note_index: int, battery_capacity: float = 500.0) -> DirectiveInterpretationEntry:
    """Deterministic, rule-based NLP extraction as high-reliability fallback."""
    raw_lower = note.lower().strip()
    t = raw_lower
    hours = parse_time_window(raw_lower)

    # 1. Solar reduction
    if any(k in raw_lower for k in ["solar", "pv", "sunlight", "photovoltaic"]):
        if any(k in raw_lower for k in ["drop", "reduc", "decreas", "fall", "loss", "cleaning", "washing", "dirt", "maintenance"]):
            factor = 0.5  # default estimate
            # Check percentage
            pct_match = re.search(r'(\d+(?:\.\d+)?)\s*%', raw_lower)
            if pct_match:
                val = float(pct_match.group(1)) / 100.0
                if "drop to" in raw_lower or "fall to" in raw_lower or "leave" in raw_lower:
                    factor = val
                elif "reduction" in raw_lower or "reduced by" in raw_lower or "drop by" in raw_lower:
                    factor = max(0.0, 1.0 - val)
                else:
                    factor = val
            elif "one-fifth" in raw_lower or "1-fifth" in raw_lower or "1/5" in raw_lower:
                factor = 0.2
            elif "one-quarter" in raw_lower or "1-quarter" in raw_lower or "1/4" in raw_lower:
                factor = 0.25
            elif "half" in raw_lower or "1/2" in raw_lower:
                factor = 0.5
            elif "one-third" in raw_lower or "1-third" in raw_lower or "1/3" in raw_lower:
                factor = 0.33

            factor = round(min(max(factor, 0.0), 1.0), 4)
            if hours:
                return DirectiveInterpretationEntry(
                    note_index=note_index,
                    applies=True,
                    directive_type="solar_reduction",
                    structured_adjustment={"hours": hours, "factor": factor},
                    explanation=f"Solar reduction interpreted: usable factor {factor} for hours {hours}."
                )

    # 2. Minimum battery reserve
    if any(k in t for k in ["reserve", "minimum", "at least"]) and any(k in t for k in ["battery", "kwh", "storage", "soc"]):
        # Extract kWh number
        kwh_match = re.search(r'(\d+(?:\.\d+)?)\s*kwh', t)
        if kwh_match:
            min_kwh = float(kwh_match.group(1))
            min_kwh = min(min_kwh, battery_capacity)
            if hours:
                return DirectiveInterpretationEntry(
                    note_index=note_index,
                    applies=True,
                    directive_type="minimum_battery_reserve",
                    structured_adjustment={"hours": hours, "minimum_energy_kwh": min_kwh},
                    explanation=f"Minimum battery reserve of {min_kwh} kWh required for hours {hours}."
                )

    # 3. No charge window
    if any(k in t for k in ["not charge", "no charg", "prevent charg", "stop charg", "avoid charg", "charging unavailable", "charge restrict"]):
        if hours:
            return DirectiveInterpretationEntry(
                note_index=note_index,
                applies=True,
                directive_type="no_charge_window",
                structured_adjustment={"hours": hours},
                explanation=f"Battery charging restricted for hours {hours}."
            )

    # 4. No discharge window
    if any(k in t for k in ["not discharge", "no discharg", "prevent discharg", "stop discharg", "avoid discharg", "discharging unavailable", "discharge restrict"]):
        if hours:
            return DirectiveInterpretationEntry(
                note_index=note_index,
                applies=True,
                directive_type="no_discharge_window",
                structured_adjustment={"hours": hours},
                explanation=f"Battery discharging restricted for hours {hours}."
            )

    # 5. Max grid window
    if any(k in t for k in ["grid", "import"]) and any(k in t for k in ["exceed", "cap", "limit", "maximum", "max"]):
        kwh_match = re.search(r'(\d+(?:\.\d+)?)\s*kwh', t)
        if kwh_match:
            max_grid = float(kwh_match.group(1))
            if hours:
                return DirectiveInterpretationEntry(
                    note_index=note_index,
                    applies=True,
                    directive_type="max_grid_window",
                    structured_adjustment={"hours": hours, "max_grid_kwh": max_grid},
                    explanation=f"Grid import capped at {max_grid} kWh for hours {hours}."
                )

    # 6. Fallback to no_op
    return DirectiveInterpretationEntry(
        note_index=note_index,
        applies=False,
        directive_type="no_op",
        structured_adjustment=None,
        explanation="Note does not affect the campus 24-hour energy schedule."
    )

def validate_and_sanitize_directive(
    raw: Dict[str, Any],
    note_index: int,
    original_note: str,
    battery_capacity: float
) -> DirectiveInterpretationEntry:
    """
    Rigorously validates raw LLM output against challenge guardrails.
    If the LLM output violates constraints, sanitize or gracefully fall back to rule-based parsing.
    """
    try:
        directive_type = raw.get("directive_type", "no_op")
        if directive_type not in ALLOWED_DIRECTIVE_TYPES:
            # Unsupported type - fall back safely to rule based
            return rule_based_extract_directive(original_note, note_index, battery_capacity)

        if directive_type == "no_op":
            return DirectiveInterpretationEntry(
                note_index=note_index,
                applies=False,
                directive_type="no_op",
                structured_adjustment=None,
                explanation=raw.get("explanation") or "Note does not affect the 24-hour energy schedule."
            )

        # For operational directives, applies MUST be True
        raw_adj = raw.get("structured_adjustment")
        if not isinstance(raw_adj, dict):
            return rule_based_extract_directive(original_note, note_index, battery_capacity)

        hours = sanitize_hours(raw_adj.get("hours", []))
        if not hours:
            # Attempt to extract hours from note if missing in LLM response
            hours = parse_time_window(original_note)
            if not hours:
                # Still no hours -> cannot enforce -> no_op
                return DirectiveInterpretationEntry(
                    note_index=note_index,
                    applies=False,
                    directive_type="no_op",
                    structured_adjustment=None,
                    explanation="No valid operational time window found."
                )

        if directive_type == "solar_reduction":
            factor_raw = raw_adj.get("factor")
            try:
                factor = float(factor_raw)
            except (ValueError, TypeError):
                factor = 0.5
            factor = min(max(factor, 0.0), 1.0)
            return DirectiveInterpretationEntry(
                note_index=note_index,
                applies=True,
                directive_type="solar_reduction",
                structured_adjustment={"hours": hours, "factor": round(factor, 4)},
                explanation=raw.get("explanation") or f"Solar output reduced to factor {factor}."
            )

        elif directive_type == "minimum_battery_reserve":
            min_energy_raw = raw_adj.get("minimum_energy_kwh")
            try:
                min_energy = float(min_energy_raw)
            except (ValueError, TypeError):
                min_energy = 50.0
            min_energy = min(max(min_energy, 0.0), battery_capacity)
            return DirectiveInterpretationEntry(
                note_index=note_index,
                applies=True,
                directive_type="minimum_battery_reserve",
                structured_adjustment={"hours": hours, "minimum_energy_kwh": round(min_energy, 2)},
                explanation=raw.get("explanation") or f"Minimum battery reserve of {min_energy} kWh."
            )

        elif directive_type in ("no_charge_window", "no_discharge_window"):
            return DirectiveInterpretationEntry(
                note_index=note_index,
                applies=True,
                directive_type=directive_type,
                structured_adjustment={"hours": hours},
                explanation=raw.get("explanation") or f"Battery {directive_type} active."
            )

        elif directive_type == "max_grid_window":
            max_grid_raw = raw_adj.get("max_grid_kwh")
            try:
                max_grid = float(max_grid_raw)
            except (ValueError, TypeError):
                max_grid = 100.0
            max_grid = max(max_grid, 0.0)
            return DirectiveInterpretationEntry(
                note_index=note_index,
                applies=True,
                directive_type="max_grid_window",
                structured_adjustment={"hours": hours, "max_grid_kwh": round(max_grid, 2)},
                explanation=raw.get("explanation") or f"Grid import capped at {max_grid} kWh."
            )

    except Exception:
        # Guarantee zero crash on untrusted LLM outputs
        return rule_based_extract_directive(original_note, note_index, battery_capacity)

    return rule_based_extract_directive(original_note, note_index, battery_capacity)
