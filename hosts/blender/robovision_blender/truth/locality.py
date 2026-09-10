"""What a correction was allowed to touch, and what it actually touched.

Iterative polish only works if a correction's blast radius is declared before it
runs and checked afterwards. Otherwise "the silhouette improved" is compatible
with "and an unrelated panel moved four centimetres", and a loop optimising the
first would happily keep doing the second. This is the measurement that separates
"the target changed and nothing else did" from "the target improved, but so did
something nobody asked about".

Reuses the host's own deep snapshot and diff rather than fingerprinting objects a
second way. A second source of truth about what changed would eventually disagree
with the one transactions and rollbacks are built on, and the disagreement would
surface as a correction that passed locality and failed to restore.

Object-level, and it says so. Face-level locality would be more useful and is not
honestly provable here: post-operation topology indices are not stable across the
operations that would need checking, so a face-level claim would be confident and
occasionally wrong. Mesh revisions are reported per changed subject, which is the
strongest granularity the host can currently stand behind.
"""
from __future__ import annotations

from typing import Any

from ..registry import HostError
from .certificate import LOWER_BETTER, NEUTRAL, Measurement

LOCALITY_SCHEMA = 1

TARGET = "target"
PROTECTED = "protected"
ALLOWED = "allowed"
UNDECLARED = "undeclared"


def _classify(identifier: str, name: str, declaration: dict[str, set[str]]) -> str:
    """Which declared set an object belongs to, by id or by name.

    Both, because a caller planning a correction knows object names and a caller
    reading a previous measurement holds ids, and forcing either to translate
    would put a lookup between the declaration and the thing it protects.
    """
    for role in (PROTECTED, TARGET, ALLOWED):
        if identifier in declaration[role] or name in declaration[role]:
            return role
    return UNDECLARED


def measure(before: dict[str, Any], after: dict[str, Any],
            declaration: dict[str, set[str]], runtime) -> Measurement:
    """Compare the state a correction started from against the state it produced."""
    from ..snapshots import diff_snapshots
    from .certificate import pins_for

    if before.get("level") != after.get("level"):
        raise HostError(
            "INCOMPARABLE_MEASUREMENTS",
            "the before and after snapshots were taken at different levels of detail",
            data={"before_level": before.get("level"), "after_level": after.get("level")},
        )

    diff = diff_snapshots(before, after)
    measurement = Measurement("locality", {})

    changed: list[dict[str, Any]] = []
    for entry in diff["changed"]:
        role = _classify(entry["id"], entry["after"].get("name", ""), declaration)
        changed.append({
            "object": entry["id"],
            "name": entry["after"].get("name"),
            "role": role,
            # The strongest granularity the host can stand behind: whether the
            # topology moved, not which faces.
            "mesh_revision_before": entry["before"].get("mesh_revision"),
            "mesh_revision_after": entry["after"].get("mesh_revision"),
        })

    created = [{"object": entry["id"], "name": entry["name"],
                "role": _classify(entry["id"], entry["name"], declaration)}
               for entry in diff["created"]]
    deleted = [{"object": entry["id"], "name": entry["name"],
                "role": _classify(entry["id"], entry["name"], declaration)}
               for entry in diff["deleted"]]

    protected_changes = [entry for entry in changed if entry["role"] == PROTECTED]
    undeclared_changes = [entry for entry in changed if entry["role"] == UNDECLARED]
    undeclared_created = [entry for entry in created if entry["role"] == UNDECLARED]
    undeclared_deleted = [entry for entry in deleted if entry["role"] == UNDECLARED]
    protected_removed = [entry for entry in deleted if entry["role"] == PROTECTED]

    measurement.metric("locality.changed_objects", len(changed), direction=NEUTRAL)
    measurement.metric("locality.target_changes",
                       sum(1 for entry in changed if entry["role"] == TARGET),
                       direction=NEUTRAL)
    measurement.metric("locality.protected_changes",
                       len(protected_changes) + len(protected_removed),
                       direction=LOWER_BETTER)
    measurement.metric("locality.undeclared_changes", len(undeclared_changes),
                       direction=LOWER_BETTER)
    measurement.metric("locality.undeclared_created", len(undeclared_created),
                       direction=LOWER_BETTER)
    measurement.metric("locality.undeclared_deleted", len(undeclared_deleted),
                       direction=LOWER_BETTER)

    # A protected object that was deleted is a protected change, not a separate
    # category of accident: the point of protecting it was that it survives.
    measurement.invariant("locality.protected_unchanged",
                          not protected_changes and not protected_removed,
                          changed=[entry["object"] for entry in protected_changes],
                          deleted=[entry["object"] for entry in protected_removed])
    measurement.invariant("locality.no_undeclared_changes", not undeclared_changes,
                          objects=[entry["object"] for entry in undeclared_changes])
    # Left-behind helper geometry is the classic quiet failure: the correction
    # worked, and the file now contains a cube nobody meant to ship.
    measurement.invariant("locality.no_undeclared_objects",
                          not undeclared_created and not undeclared_deleted,
                          created=[entry["name"] for entry in undeclared_created],
                          deleted=[entry["name"] for entry in undeclared_deleted])

    measurement.subjects = [
        {"object": "__declared__", "mesh_revision": None,
         "targets": sorted(declaration[TARGET]),
         "protected": sorted(declaration[PROTECTED]),
         "allowed": sorted(declaration[ALLOWED])},
        {"object": "__observed__", "mesh_revision": None,
         "changed": changed, "created": created, "deleted": deleted},
    ]
    measurement.unmeasured(
        "locality.element_granularity",
        "locality is measured per object; post-operation topology indices are not "
        "stable enough across operations for a face-level claim to be honest")
    measurement.notes.append(
        "computed from the host's own deep snapshot diff, the same one transactions "
        "and rollbacks are verified against")

    measurement.pins = pins_for(runtime, [])
    measurement.pins["locality"] = {
        "schema": LOCALITY_SCHEMA,
        "before_fingerprint": diff["before_fingerprint"],
        "after_fingerprint": diff["after_fingerprint"],
        "level": before.get("level"),
        "unchanged": diff["equal"],
    }
    return measurement


def resolve(params: dict[str, Any]) -> dict[str, set[str]]:
    """Read the declaration, refusing one that contradicts itself.

    An object that is both a target and protected is not a stricter declaration,
    it is an unanswerable one: whichever way it were resolved the caller would
    believe the opposite half.
    """
    declaration: dict[str, set[str]] = {}
    for role in (TARGET, PROTECTED, ALLOWED):
        raw = params.get(role + "s", [])
        if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
            raise HostError("INVALID_PARAMS", f"{role}s must be an array of strings")
        declaration[role] = set(raw)
    overlap = declaration[TARGET] & declaration[PROTECTED]
    if overlap:
        raise HostError(
            "INVALID_PARAMS",
            "an object cannot be both a target and protected",
            data={"objects": sorted(overlap)},
        )
    if not declaration[TARGET]:
        raise HostError(
            "INVALID_PARAMS",
            "targets is required: a correction with no declared blast radius cannot "
            "be checked for staying inside it",
        )
    return declaration
