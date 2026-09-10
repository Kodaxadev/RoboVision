# Host contract

Every RoboVision editor host implements the same behavioral contract even when editor APIs differ.

## Required substrate

1. `system.ping`
2. `system.hello`, publishing every pin a strict autonomous call must supply —
   world incarnation, coordinate contract, units, state domain, authored
   revision and journal position — so an external client never hard-codes one
3. `system.health`, the structured readiness report
4. `system.capabilities`
5. scene revision tracking and stale-write rejection
6. stable authoring-time object identity
7. `scene.describe`
8. `scene.snapshot`
9. `scene.diff`
10. object inspect/create/delete/transform/duplicate/parent
11. at least one visual capture path with view metadata
12. validation primitives
13. transaction begin/commit/rollback with restoration verification
14. structured exceptions; no raw traceback as the only response

## Mutation rule

A mutation must either:

- complete against declared preconditions and report the resulting revision, or
- make no intentional change and return a typed error.

If the host cannot prove atomicity, it must say so in capability metadata. Multi-step workflows belong inside an explicit transaction.

## Identity rule

Names are labels, never identity. Array/list indices count as element references only when paired with a revision that invalidates them after structural changes.

## Operator/context rule

Editor operators that depend on selection, active object, active window, mode, scene view, or ambient state run through a context adapter that supplies and restores explicit context. Prefer direct data APIs when the editor supports the operation without an operator.

## Perception rule

A screenshot with unknown camera state is incomplete evidence. A visual capture records enough metadata to reconstruct what was viewed.

## Security rule

Host listeners bind to loopback by default. Arbitrary code execution is absent/disabled by default. Any future remote transport requires authentication and explicit opt-in.
