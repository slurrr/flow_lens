# FL-0060 – Multi-Source Price Selector (Policy-Pluggable, Priority-Sticky Default)

## Decision

With multi-venue sources, Flow Lens must select a reference price series in a way that is:

- conservative (prefers continuity),
- deterministic,
- auditable (switches are logged with reasons),
- decoupled from effort inclusion (a source may contribute effort without being the active price series).

Implementation requirements:

1) Price selection is implemented behind a policy interface.
2) Phase 1 default policy is `priority_sticky`:
   - prefer fresh spot-eligible sources;
   - within eligible group, pick highest `price_priority`;
   - maintain stickiness until staleness threshold is breached;
   - fail over to perp only on stale breach + hysteresis;
   - recover to preferred source only after recovery hysteresis.
3) Deterministic tie-break when `price_priority` ties:
   - `price_priority`, then lexical `source_id`.
4) Every tick must report `active_price_source_id` (diagnostics).
5) Every switch must emit a structured switch log row with:
   - `from_source_id`, `to_source_id`,
   - `reason` (`stale`, `recovered`, `priority`, `manual_override` if ever added),
   - `staleness_from_ms`, `staleness_to_ms`,
   - `priority_from`, `priority_to`,
   - `selector_policy`.

Defaults (Phase 1, `priority_sticky`):

- `stale_failover_ms = 6000`
- `recovery_confirm_cycles = 2`
- `switch_cooldown_cycles = 1 update cycle`

Forward-compatibility:

- Phase 2 may introduce `leader_sticky` (tourney/leader-informed target with stickiness) as an alternate selector policy
  without changing diagnostics fields or switch logging schema.

## Rationale

Multi-source price selection is a hidden lever: if it churns, it can create phantom regime shifts that look like structural
changes in the lens.

Making selection conservative, deterministic, and fully logged prevents “looks wrong but math says fine” failures and keeps
replay auditing possible.

## Status

Accepted (Phase 1). New invariant for multi-venue plumbing.

