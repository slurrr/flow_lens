# FL-0051 – Persistence Phase 1 Experiment A (M1 structural memory)

## Decision

Phase 1 persistence uses an opposition-primary structural memory model: `S_t` integrates a configurable persistence input (`persist_input`, default `y_gated`) with no time-decay term in quiet periods, and unwinds primarily through opposing signed input.

The update is dt-explicit using `persist_gain_per_second` and logs per-tick persistence diagnostics (`persist_dt_s`, `persist_step_coeff`, `persist_update_mode`, input metadata) for replay validation.

## Rationale

This aligns persistence with the lens goal of showing acceptance/rejection accumulation across windows while preserving instantaneous dot semantics. It avoids leaky fixed-point suppression from prior build/decay taus and makes replay-based falsification possible with explicit timebase diagnostics.

## Status

Accepted (Experimental)
