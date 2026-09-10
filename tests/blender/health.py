"""What health must keep separate, measured against a real Blender host.

The whole value of a readiness report is that its answers are independent. A
single red/green light would collapse exactly the distinctions the state model
was built to preserve, so each case here moves one thing and checks that the
others did not move with it:

- an uncertain journal degrades incremental polling and leaves an authoritative
  observation perfectly available
- a background editor cannot render, cannot carry out a transactional
  correction, and can still author an ordinary mutation
- a transaction owned by somebody else blocks mutation and not observation
- an orphan blocks a new correction and asks to be adopted, without the host
  ever claiming the caller can do the adopting
- and health itself authors nothing to establish any of it

Driven in process through `dispatch`, because the connection id is what decides
ownership and this is where a second one can be produced deliberately.
"""
from __future__ import annotations

import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _harness import (  # noqa: E402
    Host,
    PROTOCOL_VERSION,
    artifact_dir,
    clean_scene,
    expect,
    missed_notification,
    run_gate,
)

FINDINGS: dict[str, object] = {}

OWNER = 0
STRANGER = 7


def health(rv: Host, *, client: int = OWNER) -> dict:
    """Ask as a named connection, because ownership is a fact about connections."""
    rv.serial += 1
    request = {
        "rv": PROTOCOL_VERSION,
        "id": f"{rv.label}-health-{rv.serial}",
        "method": "system.health",
        "params": {},
    }
    response = rv.runtime.dispatch(request, client_id=client)
    rv.calls.append({"request": request, "response": response})
    expect(response.get("ok") is True, f"system.health failed: {response}")
    return response["result"]


def ready(report: dict, name: str) -> dict:
    return report["ready_for"][name]


# ------------------------------------------------------------------ baseline


def a_readable_scene_is_ready_to_observe(rv: Host) -> None:
    clean_scene()
    rv.result("object.create", {"kind": "cube", "name": "Anchor"})
    report = health(rv)

    semantic = ready(report, "semantic_observation")
    FINDINGS["semantic_status"] = semantic["status"]
    FINDINGS["semantic_basis"] = semantic.get("basis")
    FINDINGS["semantic_fingerprint_matches"] = (
        semantic.get("fingerprint") == rv.fingerprint())
    FINDINGS["identity_world"] = report["identity"]["world_incarnation"]
    FINDINGS["identity_contract"] = report["identity"]["coordinate_contract"]
    FINDINGS["identity_domain"] = report["identity"]["state_domain"]
    FINDINGS["identity_resumption"] = report["identity"]["world_resumption"]
    FINDINGS["executor_status"] = report["subsystems"]["executor"]["status"]
    FINDINGS["executor_basis"] = report["subsystems"]["executor"]["basis"]
    FINDINGS["queue_depth"] = report["subsystems"]["executor"]["queue_depth"]
    FINDINGS["semantic_verify_validators"] = ready(report, "semantic_verify")["validators"]
    FINDINGS["visual_verify_metrics"] = ready(report, "visual_verify")["metrics"]


def health_authors_nothing(rv: Host) -> None:
    """An observation that had to change something to happen is not an observation."""
    before_revision = rv.runtime.revision
    before_fingerprint = rv.fingerprint()
    before_objects = len(bpy.data.objects)
    before_epoch = rv.runtime.journal.epoch

    for _ in range(3):
        health(rv)

    FINDINGS["health_revision_moved"] = rv.runtime.revision != before_revision
    FINDINGS["health_fingerprint_moved"] = rv.fingerprint() != before_fingerprint
    FINDINGS["health_object_count_moved"] = len(bpy.data.objects) != before_objects
    FINDINGS["health_epoch_moved"] = rv.runtime.journal.epoch != before_epoch


# -------------------------------------------------------------------- journal


def journal_uncertainty_does_not_condemn_the_scene(rv: Host) -> None:
    """A history the host cannot vouch for says nothing about whether it can read.

    The change is a real one the host can see and genuinely cannot attribute —
    moving the frame moves the fingerprint without touching any object — rather
    than a symptom faked by reaching into the journal.
    """
    clean_scene()
    rv.result("object.create", {"kind": "cube", "name": "Anchor"})
    rv.result("scene.snapshot")
    FINDINGS["journal_certain_before"] = rv.runtime.journal.certain

    with missed_notification():
        bpy.context.scene.frame_current = 4242
    # Nothing has been authoritative yet, so the host has not looked. The
    # mutation's resync is what finds it.
    rv.call("object.create", {"kind": "cube", "name": "Trigger"})
    expect(rv.runtime.journal.certain is False,
           "precondition: the journal must have lost certainty")

    report = health(rv)
    journal = report["subsystems"]["journal"]
    FINDINGS["journal_status"] = journal["status"]
    FINDINGS["journal_certain"] = journal["certain"]
    FINDINGS["incremental_status"] = journal["incremental_changes"]["status"]
    FINDINGS["incremental_reason"] = journal["incremental_changes"].get("reason")
    FINDINGS["uncertain_semantic"] = ready(report, "semantic_observation")["status"]
    FINDINGS["uncertain_mutate"] = ready(report, "mutate")["status"]
    FINDINGS["uncertain_begin"] = ready(report, "begin_correction")["status"]
    FINDINGS["uncertain_overall"] = report["status"]

    # Health must not quietly restore what only a full authoritative read may.
    FINDINGS["health_restored_certainty"] = rv.runtime.journal.certain


# --------------------------------------------------------------- transactions


def an_owned_transaction_is_not_an_obstacle_to_its_owner(rv: Host) -> None:
    clean_scene()
    rv.result("scene.snapshot")
    idle = health(rv)
    FINDINGS["idle_situation"] = ready(idle, "finish_or_recover")["situation"]
    FINDINGS["idle_finish_status"] = ready(idle, "finish_or_recover")["status"]
    FINDINGS["idle_begin"] = ready(idle, "begin_correction")["status"]

    transaction = rv.result("transaction.begin", {"label": "owned"})["transaction"]
    report = health(rv)
    finish = ready(report, "finish_or_recover")
    FINDINGS["owned_situation"] = finish["situation"]
    FINDINGS["owned_mutate"] = ready(report, "mutate")["status"]
    FINDINGS["owned_mutate_basis"] = ready(report, "mutate").get("basis")
    FINDINGS["owned_begin"] = ready(report, "begin_correction")["status"]
    FINDINGS["owned_begin_reason"] = ready(report, "begin_correction").get("reason")
    FINDINGS["owned_semantic"] = ready(report, "semantic_observation")["status"]
    # Never, on any path: the host cannot know what a caller is holding.
    FINDINGS["owned_claims_recovery"] = "recoverable_by_this_session" in str(report)
    FINDINGS["owned_leaks_credential"] = "recovery_token" in str(report)

    rv.result("transaction.discard", {"transaction": transaction})
    after = health(rv)
    FINDINGS["after_finish_situation"] = ready(after, "finish_or_recover")["situation"]
    FINDINGS["after_recently_finished"] = (
        ready(after, "finish_or_recover").get("recently_finished") or {}).get("state")


def a_foreign_transaction_blocks_mutation_and_not_observation(rv: Host) -> None:
    clean_scene()
    rv.result("scene.snapshot")
    transaction = rv.result("transaction.begin", {"label": "someone else's"})["transaction"]

    report = health(rv, client=STRANGER)
    FINDINGS["foreign_situation"] = ready(report, "finish_or_recover")["situation"]
    FINDINGS["foreign_mutate"] = ready(report, "mutate")["status"]
    FINDINGS["foreign_mutate_reason"] = ready(report, "mutate").get("reason")
    FINDINGS["foreign_semantic"] = ready(report, "semantic_observation")["status"]
    FINDINGS["foreign_begin_reason"] = ready(report, "begin_correction").get("reason")
    # The stranger's own refusal, so health and dispatch agree about the world.
    refused = rv.runtime.dispatch(
        {"rv": PROTOCOL_VERSION, "id": "foreign-mutate", "method": "object.create",
         "params": {"kind": "cube", "name": "Foreign"}}, client_id=STRANGER)
    FINDINGS["foreign_refusal_code"] = refused.get("error", {}).get("code")

    rv.result("transaction.discard", {"transaction": transaction})


def an_orphan_asks_to_be_adopted_without_offering_adoption(rv: Host) -> None:
    clean_scene()
    rv.result("scene.snapshot")
    transaction = rv.result("transaction.begin", {"label": "orphan"})["transaction"]
    # The owning connection goes away, which is the host's real notification.
    rv.runtime.transactions.client_disconnected(OWNER)

    report = health(rv, client=STRANGER)
    finish = ready(report, "finish_or_recover")
    FINDINGS["orphan_situation"] = finish["situation"]
    FINDINGS["orphan_finish_status"] = finish["status"]
    FINDINGS["orphan_adoption_required"] = finish["transaction"]["adoption_required"]
    FINDINGS["orphan_mutate"] = ready(report, "mutate")["status"]
    FINDINGS["orphan_mutate_reason"] = ready(report, "mutate").get("reason")
    FINDINGS["orphan_begin"] = ready(report, "begin_correction")["status"]
    FINDINGS["orphan_semantic"] = ready(report, "semantic_observation")["status"]
    FINDINGS["orphan_claims_recovery"] = "recoverable_by_this_session" in str(report)
    FINDINGS["orphan_note"] = finish.get("note")

    rv.runtime.transactions.abandon(reason="gate_teardown")
    del transaction


# ------------------------------------------------------------------ viewport


def a_background_editor_cannot_render_and_can_still_author(rv: Host) -> None:
    """Three capabilities, three answers. Collapsing them would idle a working agent.

    Rendering, correcting and mutating fail here for unrelated reasons and are
    reported separately. The correction one is the subtle case: background
    Blender's `transaction.begin` succeeds and its checkpoint is real, so the
    tempting answer is `degraded`. It is not. A correction is transactional —
    observe, propose, observe again, then commit or roll back with proof — and
    without a provable rollback the reject branch of that contract does not
    exist. A capability missing one of its two outcomes is blocked, and the
    reason says which mechanism is absent rather than implying the editor is ill.
    """
    expect(bpy.app.background, "precondition: this gate runs in background Blender")
    clean_scene()
    report = health(rv)

    visual = ready(report, "visual_observation")
    FINDINGS["background_visual"] = visual["status"]
    FINDINGS["background_visual_reason"] = visual.get("reason")
    FINDINGS["background_visual_verify"] = ready(report, "visual_verify")["status"]
    FINDINGS["background_semantic"] = ready(report, "semantic_observation")["status"]
    FINDINGS["background_mutate"] = ready(report, "mutate")["status"]
    FINDINGS["background_begin"] = ready(report, "begin_correction")["status"]
    FINDINGS["background_begin_reason"] = ready(report, "begin_correction").get("reason")
    FINDINGS["background_begin_verified_rollback"] =         ready(report, "begin_correction").get("verified_rollback")
    FINDINGS["background_semantic_verify"] = ready(report, "semantic_verify")["status"]
    FINDINGS["background_perception_multi_pass"] = \
        report["subsystems"]["perception"]["multi_pass"]

    # The mutation the report said was available really is available. Blocking a
    # correction must not have cost the ordinary mutation path, which is a
    # different capability with a different contract.
    created = rv.call("object.create", {"kind": "cube", "name": "AuthoredInBackground"})
    FINDINGS["background_mutation_outcome"] = created.get("outcome")

    # And the mechanism the block named is genuinely the one that is missing,
    # measured through the real verbs rather than asserted from the report: the
    # begin succeeds, and the rollback that would reject a candidate does not.
    #
    # The candidate has to be a real one. A rollback of a transaction that
    # changed nothing succeeds here in zero undo steps, because the scene already
    # hashes to the begin fingerprint and there is nothing to restore — which is
    # precisely why "can this host roll back?" cannot be answered by trying it on
    # an empty transaction, and why a correction is the capability that needs the
    # mechanism.
    transaction = rv.result("transaction.begin", {"label": "unrejectable"})["transaction"]
    FINDINGS["background_begin_call_succeeds"] = bool(transaction)
    empty = rv.call("transaction.rollback", {"transaction": transaction})
    FINDINGS["background_empty_rollback_steps"] = empty["result"]["undo_steps"]

    transaction = rv.result("transaction.begin", {"label": "candidate"})["transaction"]
    rv.result("object.create", {"kind": "cube", "name": "Candidate"})
    refused = rv.call("transaction.rollback", {"transaction": transaction}, ok=False)
    FINDINGS["background_rollback_code"] = refused.get("error", {}).get("code")
    FINDINGS["background_candidate_survived_rejection"] = "Candidate" in bpy.data.objects
    rv.result("transaction.discard", {"transaction": transaction})


# ---------------------------------------------------------------------- main


def main() -> None:
    rv = Host("health")
    a_readable_scene_is_ready_to_observe(rv)
    health_authors_nothing(rv)
    journal_uncertainty_does_not_condemn_the_scene(rv)
    an_owned_transaction_is_not_an_obstacle_to_its_owner(rv)
    a_foreign_transaction_blocks_mutation_and_not_observation(rv)
    an_orphan_asks_to_be_adopted_without_offering_adoption(rv)
    a_background_editor_cannot_render_and_can_still_author(rv)

    expect(FINDINGS["semantic_status"] == "ready",
           f"a readable scene was not ready to observe: {FINDINGS['semantic_status']}")
    expect(FINDINGS["semantic_basis"] == "authoritative_resync_succeeded",
           "semantic readiness was claimed without the resync that earns it")
    expect(FINDINGS["semantic_fingerprint_matches"],
           "health reported a fingerprint that did not belong to the state it read")
    expect(str(FINDINGS["identity_contract"]).startswith("rvcoord:"),
           "health did not publish a pinnable coordinate contract")
    expect(FINDINGS["identity_domain"] == "authored", "Blender reported a non-authored domain")
    expect(FINDINGS["identity_resumption"] == "not_applicable",
           "Blender claimed a world resumption it cannot prove")
    expect(FINDINGS["executor_status"] == "ready", "a call that ran reported no executor")
    expect(FINDINGS["queue_depth"] == "not_applicable",
           "a queue metric was manufactured where none exists")
    expect(FINDINGS["semantic_verify_validators"] == "not_implemented",
           "health claimed geometry validators that do not exist")
    expect(FINDINGS["visual_verify_metrics"] == "not_implemented",
           "health claimed visual metrics that do not exist")

    for moved in ("health_revision_moved", "health_fingerprint_moved",
                  "health_object_count_moved", "health_epoch_moved"):
        expect(FINDINGS[moved] is False, f"system.health perturbed the editor: {moved}")

    expect(FINDINGS["journal_status"] == "degraded",
           "an uncertain journal was reported as healthy")
    expect(FINDINGS["incremental_status"] == "degraded",
           "incremental polling was offered over an uncertain journal")
    expect(FINDINGS["incremental_reason"] == "journal_uncertain",
           f"the degradation was not attributed: {FINDINGS['incremental_reason']}")
    expect(FINDINGS["uncertain_semantic"] == "ready",
           "journal uncertainty was allowed to condemn the authoritative scene read")
    expect(FINDINGS["uncertain_mutate"] == "ready",
           "journal uncertainty blocked a mutation it has nothing to do with")
    expect(FINDINGS["health_restored_certainty"] is False,
           "health restored journal certainty, which only a full authoritative read may do")

    expect(FINDINGS["idle_situation"] == "none", "an idle host invented a transaction")
    expect(FINDINGS["idle_finish_status"] == "not_applicable",
           "an idle host offered something to finish or recover")
    # This gate runs headless, where a rollback cannot be proved, so an idle host
    # is blocked from correcting for that reason and no other.
    expect(FINDINGS["idle_begin"] == "blocked",
           f"a host that cannot prove a rollback offered a correction: {FINDINGS['idle_begin']}")
    expect(FINDINGS["owned_situation"] == "active_owned_by_this_connection",
           f"an owned transaction was misrepresented: {FINDINGS['owned_situation']}")
    expect(FINDINGS["owned_mutate"] == "ready", "the owner was told it could not mutate")
    expect(FINDINGS["owned_mutate_basis"] == "transaction_owned_by_this_connection",
           "the owner's readiness was not attributed to its ownership")
    expect(FINDINGS["owned_begin"] == "blocked",
           "a second correction was offered while one was open")
    expect(FINDINGS["owned_begin_reason"] == "transaction_active_owned_by_this_connection",
           f"the blocking state was not named: {FINDINGS['owned_begin_reason']}")
    expect(FINDINGS["owned_claims_recovery"] is False,
           "the host claimed to know whether the caller can recover")
    expect(FINDINGS["owned_leaks_credential"] is False, "a credential appeared in health")
    expect(FINDINGS["after_finish_situation"] == "none",
           "a discarded transaction was still reported as open")
    expect(FINDINGS["after_recently_finished"] == "abandoned",
           f"the recently finished record was lost: {FINDINGS['after_recently_finished']}")

    expect(FINDINGS["foreign_situation"] == "active_foreign",
           f"another connection's transaction was misrepresented: {FINDINGS['foreign_situation']}")
    expect(FINDINGS["foreign_mutate"] == "blocked", "a stranger was told it could mutate")
    expect(FINDINGS["foreign_mutate_reason"] == "transaction_active_foreign",
           f"the block was not attributed: {FINDINGS['foreign_mutate_reason']}")
    expect(FINDINGS["foreign_semantic"] == "ready",
           "a foreign transaction was allowed to block observation")
    expect(FINDINGS["foreign_refusal_code"] == "TRANSACTION_FOREIGN",
           f"health and dispatch disagreed about the stranger: {FINDINGS['foreign_refusal_code']}")

    expect(FINDINGS["orphan_situation"] == "orphaned_adoption_required",
           f"an orphan was misrepresented: {FINDINGS['orphan_situation']}")
    expect(FINDINGS["orphan_adoption_required"] is True, "the orphan did not ask to be adopted")
    expect(FINDINGS["orphan_mutate"] == "blocked", "an orphan did not block mutation")
    expect(FINDINGS["orphan_mutate_reason"] == "transaction_orphaned_adoption_required",
           f"the block was not attributed: {FINDINGS['orphan_mutate_reason']}")
    expect(FINDINGS["orphan_begin"] == "blocked", "a new correction was offered over an orphan")
    expect(FINDINGS["orphan_semantic"] == "ready", "an orphan was allowed to block observation")
    expect(FINDINGS["orphan_claims_recovery"] is False,
           "the host told a stranger it could recover the orphan")
    expect("credential" in (FINDINGS["orphan_note"] or ""),
           "health did not say who is unable to vouch for the credential")

    expect(FINDINGS["background_visual"] == "blocked",
           "background Blender claimed an interactive viewport")
    expect(FINDINGS["background_visual_reason"] == "background_mode",
           f"the visual block was not attributed: {FINDINGS['background_visual_reason']}")
    expect(FINDINGS["background_visual_verify"] == "blocked",
           "visual verification was offered without a visual observation")
    expect(FINDINGS["background_semantic"] == "ready",
           "a missing viewport was allowed to condemn the semantic read")
    expect(FINDINGS["background_mutate"] == "ready",
           "background Blender was told it could not author")
    expect(FINDINGS["background_begin"] == "blocked",
           f"a correction with no reject branch was offered as usable: "
           f"{FINDINGS['background_begin']}")
    expect(FINDINGS["background_begin_reason"] == "verified_rollback_unavailable",
           f"the missing mechanism was not named: {FINDINGS['background_begin_reason']}")
    expect(FINDINGS["background_begin_verified_rollback"] is False,
           "the block did not state which mechanism is unavailable as a fact")
    expect(FINDINGS["background_semantic_verify"] == "ready",
           "blocking a correction was allowed to condemn semantic verification")
    expect(FINDINGS["background_mutation_outcome"] == "applied",
           "health said mutation was ready and it was not")
    expect(FINDINGS["background_begin_call_succeeds"],
           "precondition: a begin must still succeed, or the block is about the begin")
    expect(FINDINGS["background_empty_rollback_steps"] == 0,
           "precondition: an empty rollback must be the trivial case, or the next "
           "assertion is not about the undo mechanism at all")
    expect(FINDINGS["background_rollback_code"] == "UNDO_UNAVAILABLE",
           f"the mechanism health said was missing was not the one that failed: "
           f"{FINDINGS['background_rollback_code']}")
    expect(FINDINGS["background_candidate_survived_rejection"],
           "precondition: the rejected candidate must still be there, which is what "
           "makes the missing rollback the reason a correction cannot be attempted")

    (artifact_dir("blender-health") / "findings.txt").write_text(
        "\n".join(f"{key}={value}" for key, value in sorted(FINDINGS.items())) + "\n",
        encoding="utf-8")


run_gate("BLENDER_HEALTH", main, "blender-health")
