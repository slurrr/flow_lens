# FL-0039 – No Historical Persistence in Engine

## Decision

The rolling event buffer is in-memory only. The engine does not persist historical event data.

## Rationale

Flow Lens is a live structural diagnostic tool. Historical storage belongs in separate systems and would add complexity unrelated to the lens purpose.

## Status

Accepted (Scope Constraint)
