# ADR-002: Mixed-Integer Linear Programming (MILP) Formulation with PuLP

## Status
Accepted

## Date
2026-09-18

## Context
The GridWise challenge requires generating a 24-hour cost-optimal energy dispatch plan ($h = 0 \dots 23$) balancing grid electricity, rooftop solar, and battery storage.

Core physical and business constraints:
1. **Hourly Energy Balance:** Supply ($\text{grid} + \text{solar\_used} + \text{discharge}$) must strictly equal Demand ($\text{demand} + \text{charge}$) every hour.
2. **Solar Curtailment:** Unused solar is curtailed; grid export is prohibited ($0 \le \text{solar\_used} \le \text{effective\_solar}$).
3. **Action Mutual Exclusivity:** The battery cannot charge and discharge simultaneously in the same hour ($\text{battery\_action} \in \{\text{"charge"}, \text{"discharge"}, \text{"idle"}\}$).
4. **End-of-Day Neutrality:** $E_{\text{after}}[23] = E_{\text{initial}}$, preventing the optimizer from artificially treating stored battery energy as free energy.
5. **Directive Modifications:** Dynamic minimum reserves, charge/discharge restriction windows, and grid import caps.

## Decision
Formulate the scheduling problem as a **Mixed-Integer Linear Program (MILP)** using **PuLP** with the **CBC** solver.

### Decision Variables (per hour $h \in \{0 \dots 23\}$)
- $g_h \ge 0$: Grid electricity imported (kWh).
- $s_h \in [0, \text{effective\_solar}[h]]$: Solar consumed (kWh).
- $c_h \in [0, \text{max\_charge}[h]]$: Battery charge transfer (kWh).
- $d_h \in [0, \text{max\_discharge}[h]]$: Battery discharge transfer (kWh).
- $z_h \in \{0, 1\}$: Binary decision variable where $z_h = 1$ indicates charging permitted, $z_h = 0$ indicates discharging permitted.
- $E_h \in [\text{min\_reserve}[h], \text{capacity}]$: Battery state of charge after hour $h$.

### Objective Function
Minimize total grid electricity cost over the 24-hour horizon:
$$\min \sum_{h=0}^{23} (g_h \times \text{tariff}[h])$$

### Key Formulation Constraints
1. **Mutual Exclusivity:**
   $$c_h \le \text{max\_charge} \times z_h$$
   $$d_h \le \text{max\_discharge} \times (1 - z_h)$$
2. **Continuity & Neutrality:**
   $$E_0 = E_{\text{initial}} + c_0 - d_0$$
   $$E_h = E_{h-1} + c_h - d_h \quad \forall h \in \{1 \dots 23\}$$
   $$E_{23} = E_{\text{initial}}$$
3. **Grid Window Cap:**
   $$g_h \le \text{max\_grid}[h]$$

## Alternatives Considered

### Pure Linear Programming (LP without binary variables)
- **Pros:** Continuous, slightly faster solve time (~2ms vs ~8ms).
- **Cons:** In degenerate hours (e.g. zero solar or matching tariffs), LP solvers can select simultaneous non-zero charge and discharge ($c_h > 0$ and $d_h > 0$), violating physical mutual exclusivity and failing judge validation.
- **Rejected:** Mutual exclusivity is a hard constraint in Section 10.3 and 11.3.

### Greedy / Rule-Based Heuristic Scheduling
- **Pros:** No external solver dependency.
- **Cons:** Cannot achieve mathematical optimality; fails the 10-point optimization quality metric:
  $$\text{score} = \min\left(1, \frac{\text{optimal\_cost}}{\text{team\_cost}}\right) \times 10$$
- **Rejected:** Yields sub-optimal schedules and loses competitive score.

### Dynamic Programming (DP)
- **Pros:** Handles non-linear cost curves.
- **Cons:** Continuous state space (energy in kWh) requires discretization, which introduces round-off error or state explosion.
- **Rejected:** MILP solves continuous linear constraints exactly with zero discretization error.

## Consequences
- The CBC solver completes execution in under 20ms for the 24-hour horizon.
- Zero risk of simultaneous charge/discharge.
- The schedule strictly guarantees end-of-day battery neutrality within numerical tolerance ($< 0.01\text{ kWh}$).
