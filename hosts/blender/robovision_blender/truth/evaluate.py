"""Whether a candidate correction should be kept, rejected, or called unproven.

The deterministic half of the Artist Loop. Deciding *what* to try is the frontier
model's job and always will be; deciding whether the attempt earned its place is
arithmetic, and arithmetic is what should be trusted with a commit.

Three rules shape everything here.

**No weighted scalar.** A single quality number invites gaming: an agent
optimising it learns that a large improvement in a cheap metric buys a small
regression in an expensive one, and eventually buys a broken mesh. Acceptance is
a vector of constraints that must all hold — hard invariants, target improvement
by epsilon, protected metrics within tolerance, locality — and any one of them
failing is enough.

**Direction comes from the measurement.** A metric already declares whether lower
or higher is better, so nothing here keeps a table of which way is good. A table
would eventually disagree with the measurement it describes, and it would
disagree in the direction of accepting a regression.

**Missing evidence is never acceptance.** A required invariant that could not be
established, a metric absent from one side, a comparison across incompatible
measurements — none of those are a candidate that passed. They are `indeterminate`
or `reject`, and the causes say which piece of evidence was missing.
"""
from __future__ import annotations

from typing import Any

from ..registry import HostError
from .certificate import comparable

EVALUATION_SCHEMA = 1

ACCEPT = "accept"
REJECT = "reject"
INDETERMINATE = "indeterminate"


AMBIGUOUS = object()


def _metric(certificates: dict[str, Any], name: str, kind: str | None = None):
    """One metric, or a refusal to guess which of several was meant.

    The same metric name legitimately appears in more than one certificate: a
    front-view and a side-view reference comparison both publish
    `reference.macro.excess_fraction`, and they are exactly the pair a policy
    wants to treat differently — improve the front, protect the side. Taking the
    first match would silently judge the wrong view, and the answer would look
    perfectly reasonable. So an unqualified name that matches twice is ambiguous,
    and a policy says which certificate it means.
    """
    if kind is not None:
        certificate = certificates.get(kind)
        if certificate is None:
            return None
        return certificate.get("metrics", {}).get(name)
    found = [certificate["metrics"][name] for certificate in certificates.values()
             if name in certificate.get("metrics", {})]
    if len(found) > 1:
        return AMBIGUOUS
    return found[0] if found else None


def _invariants(certificates: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Every invariant, and a repeated name holds only if every copy holds.

    Merging by overwrite would let one passing measurement hide another's
    failure purely by iteration order, which is the wrong direction for a gate.
    """
    merged: dict[str, dict[str, Any]] = {}
    for certificate in certificates.values():
        for name, entry in certificate.get("invariants", {}).items():
            if name in merged and merged[name]["holds"] and not entry["holds"]:
                merged[name] = entry
            elif name not in merged:
                merged[name] = entry
    return merged


def _limits(certificates: dict[str, Any]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for certificate in certificates.values():
        merged.update(certificate.get("limits", {}))
    return merged


def _check_comparable(before: dict[str, Any], after: dict[str, Any],
                      causes: list[dict[str, Any]]) -> None:
    """Both sides must describe the same subjects in the same world and frame.

    A rejection rather than a warning, and rather than `indeterminate`: a
    correction whose result cannot be verified must not be kept, and the safe
    outcome of "I cannot tell" is to roll back.
    """
    for kind, certificate in after.items():
        if kind not in before:
            causes.append({"cause": "measurement_incompatibility",
                           "detail": f"no {kind} measurement was taken before the correction",
                           "kind": kind})
            continue
        ok, reason = comparable(before[kind], certificate)
        if not ok:
            causes.append({"cause": "measurement_incompatibility",
                           "detail": reason, "kind": kind})


def _check_invariants(before: dict[str, Any], after: dict[str, Any], required,
                      reject: list[dict[str, Any]],
                      indeterminate: list[dict[str, Any]]) -> list[str]:
    """Hard gates. A target improvement never compensates for broken geometry."""
    holding = _invariants(after)
    unmeasured = _limits(after)
    names = sorted(holding) if required == "all" else list(required)
    checked = []
    for name in names:
        if name in unmeasured:
            indeterminate.append({"cause": "invariant_unmeasured", "invariant": name,
                                  "detail": unmeasured[name]})
            continue
        entry = holding.get(name)
        if entry is None:
            indeterminate.append({"cause": "invariant_missing", "invariant": name})
            continue
        checked.append(name)
        if not entry["holds"]:
            was = _invariants(before).get(name, {}).get("holds")
            reject.append({
                "cause": "failed_invariant", "invariant": name,
                "evidence": entry.get("evidence"),
                # Whether the candidate broke it or merely failed to fix it. Both
                # block acceptance; they call for very different next attempts.
                "already_failing_before": was is False,
            })
    return checked


def _check_targets(before: dict[str, Any], after: dict[str, Any], targets,
                   reject: list[dict[str, Any]],
                   indeterminate: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every declared target must move the way its own metric says is better."""
    achieved = []
    for declaration in targets:
        name = declaration["metric"]
        kind = declaration.get("kind")
        epsilon = declaration["epsilon"]
        was, now = _metric(before, name, kind), _metric(after, name, kind)
        if was is AMBIGUOUS or now is AMBIGUOUS:
            indeterminate.append({"cause": "target_metric_ambiguous", "metric": name,
                                  "remedy": "name the measurement kind this target is "
                                            "about; the same metric appears in more "
                                            "than one certificate"})
            continue
        if was is None or now is None:
            indeterminate.append({"cause": "target_metric_missing", "metric": name,
                                  "kind": kind,
                                  "before": was is not None, "after": now is not None})
            continue
        direction = now["direction"]
        if direction == "neutral":
            # A metric with no better direction cannot be a target: nothing here
            # could decide whether it moved the right way.
            indeterminate.append({"cause": "target_metric_has_no_direction",
                                  "metric": name})
            continue
        delta = now["value"] - was["value"]
        improved = -delta if direction == "lower_better" else delta
        record = {"metric": name, "kind": kind,
                  "before": was["value"], "after": now["value"],
                  "delta": round(delta, 9), "direction": direction,
                  "required_improvement": epsilon,
                  "achieved_improvement": round(improved, 9)}
        if improved < epsilon:
            reject.append({"cause": "insufficient_target_improvement", **record})
        else:
            achieved.append(record)
    return achieved


def _check_protected(before: dict[str, Any], after: dict[str, Any], protected,
                     reject: list[dict[str, Any]],
                     indeterminate: list[dict[str, Any]]) -> None:
    """Declared protections may drift within tolerance and no further."""
    for declaration in protected:
        name = declaration["metric"]
        kind = declaration.get("kind")
        tolerance = declaration["tolerance"]
        was, now = _metric(before, name, kind), _metric(after, name, kind)
        if was is AMBIGUOUS or now is AMBIGUOUS:
            indeterminate.append({"cause": "protected_metric_ambiguous", "metric": name,
                                  "remedy": "name the measurement kind this protection "
                                            "is about"})
            continue
        if was is None or now is None:
            indeterminate.append({"cause": "protected_metric_missing", "metric": name,
                                  "kind": kind})
            continue
        delta = now["value"] - was["value"]
        direction = now["direction"]
        # A neutral metric is protected against movement in either direction: a
        # dimension that must not change is not a dimension that may only grow.
        regression = abs(delta) if direction == "neutral" else (
            delta if direction == "lower_better" else -delta)
        if regression > tolerance:
            reject.append({
                "cause": "protected_regression", "metric": name, "kind": kind,
                "before": was["value"], "after": now["value"],
                "delta": round(delta, 9), "direction": direction,
                "tolerance": tolerance, "regression": round(regression, 9),
            })


def _check_locality(locality: dict[str, Any] | None, required: bool,
                    reject: list[dict[str, Any]],
                    indeterminate: list[dict[str, Any]]) -> None:
    if locality is None:
        if required:
            indeterminate.append({"cause": "locality_not_measured"})
        return
    for name, entry in sorted(locality.get("invariants", {}).items()):
        if not entry["holds"]:
            reject.append({"cause": "locality_violation", "invariant": name,
                           "evidence": entry.get("evidence")})


def evaluate(before: dict[str, Any], after: dict[str, Any],
             policy: dict[str, Any]) -> dict[str, Any]:
    """The decision, and every reason behind it."""
    reject: list[dict[str, Any]] = []
    indeterminate: list[dict[str, Any]] = []

    _check_comparable(before, after, reject)
    checked = _check_invariants(before, after, policy["required_invariants"],
                                reject, indeterminate)
    achieved = _check_targets(before, after, policy["targets"], reject, indeterminate)
    _check_protected(before, after, policy["protected"], reject, indeterminate)
    _check_locality(policy.get("locality"), policy.get("require_locality", False),
                    reject, indeterminate)

    # Rejection outranks indeterminacy: something is definitely wrong, and
    # reporting "unproven" for a candidate that broke a gate would understate it.
    if reject:
        decision = REJECT
    elif indeterminate:
        decision = INDETERMINATE
    else:
        decision = ACCEPT

    advisory = []
    for name in policy.get("advisory", []):
        was, now = _metric(before, name), _metric(after, name)
        if was is None or now is None or was is AMBIGUOUS or now is AMBIGUOUS:
            continue
        advisory.append({"metric": name, "before": was["value"], "after": now["value"],
                         "delta": round(now["value"] - was["value"], 9),
                         "direction": now["direction"]})

    return {
        "schema": EVALUATION_SCHEMA,
        "decision": decision,
        "accepted": decision == ACCEPT,
        "reject_causes": reject,
        "indeterminate_causes": indeterminate,
        "targets_achieved": achieved,
        "invariants_checked": checked,
        # Recorded, never gating. A metric a policy did not name must not quietly
        # acquire the power to reject a correction.
        "advisory": advisory,
        "policy": {
            "targets": policy["targets"],
            "protected": policy["protected"],
            "required_invariants": policy["required_invariants"],
            "require_locality": policy.get("require_locality", False),
        },
        "certificates": {
            "before": {kind: entry.get("certificate") for kind, entry in before.items()},
            "after": {kind: entry.get("certificate") for kind, entry in after.items()},
            "locality": (policy.get("locality") or {}).get("certificate"),
        },
        "note": "acceptance is a vector of constraints; there is deliberately no "
                "weighted quality score, because a scalar can be gamed by trading "
                "an expensive regression for a cheap improvement",
    }


def resolve_policy(params: dict[str, Any]) -> dict[str, Any]:
    """Read and validate the acceptance policy, refusing an empty one."""
    targets = params.get("targets", [])
    protected = params.get("protected", [])
    if not isinstance(targets, list) or not targets:
        raise HostError(
            "INVALID_PARAMS",
            "targets is required: a correction with nothing it was trying to improve "
            "cannot be judged to have succeeded",
        )
    for entry in targets:
        if not isinstance(entry, dict) or "metric" not in entry:
            raise HostError("INVALID_PARAMS", "each target needs a metric")
        entry.setdefault("epsilon", 0.0)
        entry["epsilon"] = float(entry["epsilon"])
        if entry["epsilon"] < 0.0:
            raise HostError("INVALID_PARAMS", "epsilon must not be negative")
    if not isinstance(protected, list):
        raise HostError("INVALID_PARAMS", "protected must be an array")
    for entry in protected:
        if not isinstance(entry, dict) or "metric" not in entry:
            raise HostError("INVALID_PARAMS", "each protected entry needs a metric")
        entry.setdefault("tolerance", 0.0)
        entry["tolerance"] = float(entry["tolerance"])
        if entry["tolerance"] < 0.0:
            raise HostError("INVALID_PARAMS", "tolerance must not be negative")

    required = params.get("required_invariants", "all")
    if required != "all":
        if not isinstance(required, list) or any(not isinstance(v, str) for v in required):
            raise HostError("INVALID_PARAMS",
                            "required_invariants must be \"all\" or an array of names")
    advisory = params.get("advisory", [])
    if not isinstance(advisory, list) or any(not isinstance(v, str) for v in advisory):
        raise HostError("INVALID_PARAMS", "advisory must be an array of metric names")

    return {
        "targets": targets,
        "protected": protected,
        "required_invariants": required,
        "advisory": advisory,
        "locality": params.get("locality"),
        "require_locality": bool(params.get("require_locality", False)),
    }
