# ADR-003: Deterministic Guardrails & Independent Post-Solve Schedule Replayer

## Status
Accepted

## Date
2026-09-18

## Context
The problem statement explicitly notes:
> "Raw LLM output must be treated as untrusted structured data until deterministic validation passes."
> "The judge independently replays the final schedule hour by hour using the effective solar and any additional operator directives."
> "A schedule that interprets a note correctly but does not apply it is still incorrect."

To achieve the maximum 25 points in Directive Application & Constraints and 10 points in API Contract, the application must:
1. Validate and sanitize extracted directives before passing them to the solver.
2. Ensure strict adherence to interval semantics ($[start, end)$ with start included, end excluded, ascending order).
3. Verify every physical, battery, and accounting constraint post-solve before returning the HTTP response.

## Decision
Implement a **two-phase verification architecture**:
1. **Pre-Optimization Guardrails ([`utils/guardrails.py`](file:///home/itshimelz/Downloads/BUP/bup_hackathon_pre/utils/guardrails.py)):**
   - Whitelist validation against the 6 approved directive types.
   - Enforce `applies = false` exclusively for `no_op`.
   - Time sanitization: Convert all time intervals into sorted, unique integer arrays within $[0, 23]$.
   - Range bounds: Solar factor $\in [0.0, 1.0]$, reserve $\le \text{capacity\_kwh}$, grid cap $\ge 0.0$.
2. **Post-Optimization Schedule Replayer ([`services/optimizer.py`](file:///home/itshimelz/Downloads/BUP/bup_hackathon_pre/services/optimizer.py)):**
   - Replay battery state transitions: $E_{\text{after}}[h] = E_{\text{before}}[h] \pm \text{battery\_kwh}$.
   - Replay hourly energy balance: $\text{grid} + \text{solar\_used} + \text{discharge} = \text{demand} + \text{charge}$.
   - Replay solar curtailment: $\text{solar\_used} \le \text{effective\_solar}$.
   - Replay battery capacity and dynamic minimum reserve bounds.
   - Replay strict end-of-day battery neutrality: $|E_{\text{after}}[23] - E_{\text{initial}}| \le 0.01\text{ kWh}$.
   - Recalculate summary metrics (`total_grid_kwh`, `total_cost_bdt`, `peak_grid_kwh`) directly from `hourly_plan`.

## Alternatives Considered

### Direct Trust in Raw LLM JSON
- **Pros:** Less validation code.
- **Cons:** LLMs occasionally output 1-based hours (1–24), reverse-sorted arrays, or hallucinate non-existent directive keys.
- **Rejected:** Leads to fatal solver crashes or immediate failure in judge evaluations.

### Trusting Solver Variables Directly Without Replay
- **Pros:** Saves ~1ms of post-processing.
- **Cons:** Minor numerical floating-point solver drift (e.g. $199.9999999$ instead of $200.0$) can fail strict judge equality checks ($|a - b| \le 0.01$).
- **Rejected:** Post-solve deterministic replayer ensures 100% precision matching the judge replayer.

## Consequences
- 100% compliance with judge replaying rules.
- Floating-point discrepancies eliminated via standardized 2-decimal rounding.
- Guaranteed protection against malformed model outputs.
