import json
import logging
from typing import List, Optional
from core.config import settings
from models.response import DirectiveInterpretationEntry
from utils.guardrails import validate_and_sanitize_directive, rule_based_extract_directive

logger = logging.getLogger("llm_client")

SYSTEM_PROMPT = """You are an expert energy operations system for the BUP Smart Campus Energy Challenge.
Your task is to analyze natural language campus operator notes and extract structured directives that affect the 24-hour energy optimization schedule.

### Supported Directive Types & JSON Schema:
1. "solar_reduction":
   When usable rooftop solar is reduced.
   "structured_adjustment": {"hours": [int, ...], "factor": float}
   - hours: unique integers 0 to 23 in strictly ascending order. Whole-hour intervals [start, end) where start is included and end is excluded. E.g., '1 PM to 3 PM' -> [13, 14]. '14:00 to 16:00' -> [14, 15].
   - factor: fraction of solar remaining between 0.0 and 1.0. E.g., '80% reduction' -> 0.2. 'drop to 25%' -> 0.25. 'one-fifth' -> 0.2.

2. "minimum_battery_reserve":
   When the battery must maintain an elevated minimum energy level in kWh.
   "structured_adjustment": {"hours": [int, ...], "minimum_energy_kwh": float}

3. "no_charge_window":
   When battery charging is disallowed.
   "structured_adjustment": {"hours": [int, ...]}

4. "no_discharge_window":
   When battery discharging is disallowed.
   "structured_adjustment": {"hours": [int, ...]}

5. "max_grid_window":
   When grid electricity import is capped at a maximum kWh.
   "structured_adjustment": {"hours": [int, ...], "max_grid_kwh": float}

6. "no_op":
   Irrelevant distractor notes or notes that do not impact the 24-hour electrical schedule (e.g., cafeteria menu changes, visitor notices, bus routes).
   "structured_adjustment": null

### Critical Rules:
- Return a JSON object with a single top-level key "directives".
- "directives" must contain exactly one entry for each operator note, in note_index order (0, 1, ... N-1).
- For "no_op", structured_adjustment must be null.
- For all other directives, structured_adjustment must match the exact schema.
- All hours arrays must be sorted unique whole-hour integers from 0 to 23.

### Few-Shot Examples:
Operator note: "Solar output will drop to about 20% from 1 PM to 3 PM."
-> {"note_index": 0, "directive_type": "solar_reduction", "structured_adjustment": {"hours": [13, 14], "factor": 0.2}, "explanation": "Solar output reduced to 20% from 13:00 to 15:00."}

Operator note: "Do not charge the battery between 2 PM and 4 PM."
-> {"note_index": 1, "directive_type": "no_charge_window", "structured_adjustment": {"hours": [14, 15]}, "explanation": "Charging disallowed between 14:00 and 16:00."}

Operator note: "Keep at least 120 kWh in reserve from 6 PM until 9 PM."
-> {"note_index": 2, "directive_type": "minimum_battery_reserve", "structured_adjustment": {"hours": [18, 19, 20], "minimum_energy_kwh": 120.0}, "explanation": "Minimum reserve set to 120 kWh for hours 18 to 20."}

Operator note: "The cafeteria menu changes tomorrow."
-> {"note_index": 3, "directive_type": "no_op", "structured_adjustment": null, "explanation": "Cafeteria notice is irrelevant to energy scheduling."}
"""

def extract_with_gemini(
    operator_notes: List[str],
    battery_capacity: float
) -> Optional[List[DirectiveInterpretationEntry]]:
    """Extract directives using Google Gemini via google-genai SDK."""
    if not settings.GEMINI_API_KEY:
        return None

    try:
        from google import genai
        from google.genai import types
        client = genai.Client(
            api_key=settings.GEMINI_API_KEY,
            http_options=types.HttpOptions(timeout=8000)
        )
        user_prompt = "Analyze and interpret these operator notes for scenario:\n"
        for idx, note in enumerate(operator_notes):
            user_prompt += f"Note {idx}: \"{note}\"\n"

        prompt = f"{SYSTEM_PROMPT}\n\n{user_prompt}"
        response = client.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=prompt,
            config={"response_mime_type": "application/json"}
        )

        content = response.text or "{}"
        data = json.loads(content)
        raw_directives = data.get("directives", [])

        indexed_directives = {}
        if isinstance(raw_directives, list):
            for entry in raw_directives:
                if isinstance(entry, dict) and "note_index" in entry:
                    indexed_directives[entry["note_index"]] = entry

        results: List[DirectiveInterpretationEntry] = []
        for idx, note in enumerate(operator_notes):
            raw_entry = indexed_directives.get(idx)
            if raw_entry:
                sanitized = validate_and_sanitize_directive(raw_entry, idx, note, battery_capacity)
                results.append(sanitized)
            else:
                results.append(rule_based_extract_directive(note, idx, battery_capacity))

        logger.info(f"Successfully extracted directives using Google Gemini ({settings.GEMINI_MODEL})")
        return results

    except Exception as e:
        logger.warning(f"Google Gemini extraction encountered an issue ({e.__class__.__name__}).")
        return None

def extract_with_groq(
    operator_notes: List[str],
    battery_capacity: float
) -> Optional[List[DirectiveInterpretationEntry]]:
    """Extract directives using Groq."""
    if not settings.GROQ_API_KEY:
        return None

    try:
        from groq import Groq
        client = Groq(api_key=settings.GROQ_API_KEY)
        user_prompt = "Analyze and interpret these operator notes for scenario:\n"
        for idx, note in enumerate(operator_notes):
            user_prompt += f"Note {idx}: \"{note}\"\n"

        chat_completion = client.chat.completions.create(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt}
            ],
            model=settings.GROQ_MODEL,
            temperature=0.0,
            response_format={"type": "json_object"},
            timeout=10.0
        )

        content = chat_completion.choices[0].message.content or "{}"
        data = json.loads(content)
        raw_directives = data.get("directives", [])

        indexed_directives = {}
        if isinstance(raw_directives, list):
            for entry in raw_directives:
                if isinstance(entry, dict) and "note_index" in entry:
                    indexed_directives[entry["note_index"]] = entry

        results: List[DirectiveInterpretationEntry] = []
        for idx, note in enumerate(operator_notes):
            raw_entry = indexed_directives.get(idx)
            if raw_entry:
                sanitized = validate_and_sanitize_directive(raw_entry, idx, note, battery_capacity)
                results.append(sanitized)
            else:
                results.append(rule_based_extract_directive(note, idx, battery_capacity))

        logger.info(f"Successfully extracted directives using Groq ({settings.GROQ_MODEL})")
        return results

    except Exception as e:
        logger.warning(f"Groq extraction encountered an issue ({e.__class__.__name__}).")
        return None

def extract_directives(
    operator_notes: List[str],
    battery_capacity: float = 500.0
) -> List[DirectiveInterpretationEntry]:
    """
    Unified extraction pipeline:
    1. Primary: Google Gemini AI Studio
    2. Secondary: Groq LLM
    3. Fallback: Deterministic rule-based NLP parser
    """
    # 1. Try Google Gemini
    gemini_res = extract_with_gemini(operator_notes, battery_capacity)
    if gemini_res is not None:
        return gemini_res

    # 2. Try Groq
    groq_res = extract_with_groq(operator_notes, battery_capacity)
    if groq_res is not None:
        return groq_res

    # 3. Deterministic rule-based fallback
    logger.info("Using deterministic rule-based fallback extraction.")
    results: List[DirectiveInterpretationEntry] = []
    for idx, note in enumerate(operator_notes):
        results.append(rule_based_extract_directive(note, idx, battery_capacity))
    return results
