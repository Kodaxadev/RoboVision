# Transactions, ownership and recovery

Part of the contract in [CROSS_EDITOR_STATE.md](CROSS_EDITOR_STATE.md), kept
separately because ownership, lifecycle states and recovery credentials are each
load-bearing on their own. Section numbers are preserved so existing references
still resolve.

## 4. Transactions are RoboVision's, not native undo's

Native undo is a human convenience and a useful mechanism, but it must not be the
whole semantic definition. A transaction tracks what it owns: objects created and
deleted, hierarchy changes, mesh datablocks touched, transforms, modifiers,
materials and nodes, components and properties, prefab changes, generated assets,
the revisions it started from, and the pre-state needed to restore.

The guarantee is: begin, mutate many entities, validate, detect a defect, roll
back, and **prove** the authoritative state was restored — proof by fingerprint
comparison, not by trusting that undo did the right thing.

### 4.1 Ownership and adoption

**Implemented on both hosts.** A transaction belongs to the connection that
opened it. Mutations from other connections are refused with
`TRANSACTION_FOREIGN`; reads stay open.

What this replaced was too weak: any client could finish an orphan once the
owner's socket dropped, which treats a dropped TCP connection as authorization to
commit or destroy someone else's half-written edit. `transaction.begin` now
returns a `recovery_token` — 32 bytes of cryptographic randomness, handed out
exactly once, stored only as a salted SHA-256 verifier and compared in constant
time. It appears in no diagnostic: not in `system.hello`, not in transaction
state, not in journal events.

```text
transaction.adopt(transaction, recovery_token)
    -> ownership + a rotated token | TRANSACTION_ADOPTION_REFUSED
transaction.discard(transaction)
    -> abandoned, for a transaction no outcome can honestly be claimed for
```

**The client precommits the credential.** Measured: with the host minting the
secret and returning it in the begin reply, a lost reply plus a dead connection
left the transaction orphaned while the caller never received the credential that
could reclaim it — safe from duplicate application and permanently stranded.
Adoption was worse, because it rotates: if its reply is lost, the old secret is
already dead and the replacement existed only in the reply that vanished.

So the client generates a 32-byte secret locally and sends only
`recovery_verifier` with `transaction.begin`; it holds the credential before the
side effect happens. Adoption proves possession of the current secret and
supplies `next_recovery_verifier` for the one it has already generated. The
authority needed after a lost reply never exists only inside that lost reply, and
no plaintext credential is ever written to a ledger. The host still mints as a
fallback for a caller that did not precommit, and says which happened —
`recovery_precommitted` — so the weaker path is visible rather than
indistinguishable.

Adoption rotates the credential, so a leaked one cannot be replayed, and any
host-minted replacement goes only to the client that just proved itself. Every refusal is
side-effect free — owner, state, verifier, scene, revision and journal all
unchanged — because an adoption attempt that changes something is a way to
attack a transaction without passing its check.

Identity is host-issued and world-scoped, `rvtx:<world>:<random>`. A
client-supplied string is a correlation label and never the lifecycle identity:
trusting a caller-chosen id would let one client name another's transaction.

**Lifecycle is an explicit state with a reason**, because "active or not" could
not express what the measured cases actually are:

| state | means | reached by |
| --- | --- | --- |
| `active` | owned by a live connection | `transaction.begin`, successful adoption |
| `orphaned` | the owner is gone; only its token can reclaim it | owner disconnect, verified bridge reload |
| `abandoned` | ended without an outcome, with a reason | world replaced, document changed, bridge reloaded, discarded |
| `committed` / `rolled_back` | finished | `transaction.commit` / `transaction.rollback` |
| `recovery_uncertain` | interrupted, and its checkpoint can no longer be reproduced | bridge reload with a session-scoped checkpoint |

Reasons are named, not free text: `owner_disconnected`, `world_replaced`,
`document_changed`, `bridge_reloaded`, `checkpoint_identities_lost`,
`discarded_by_client`. `system.hello` reports the state, the world, the owner,
whether adoption is required and whether a verified rollback is available —
which is what health will consume rather than reinterpreting internals.

**Contamination is not ownership,** and the two are answered in that order.
Ownership says who is authorised to act; contamination says whether acting can
still claim what it will affect. A correctly adopted owner can still be refused
for contamination, `force` is the authorised owner accepting a risk rather than
a privilege, and an unauthorised client never reaches the point where it would
mean anything.

**Supervisor adoption is deliberately unavailable.** The architecture describes a
supervisor client with elevated recovery authority, and it stays a described
capability until there is a real authentication layer to hang it on: the host is
loopback transport with no capability model, so any "supervisor token" invented
now would be an unauthenticated magic string wearing the word authorization. The
hook is the same `transaction.adopt` verb — a supervisor presents a credential
the capability layer issues, rather than the token minted at begin — and until
that layer exists there is no way to adopt without the owner's token. A missing
elevated feature is better than an unauthenticated one.

- the owner reconnecting with its recovery token adopts its own transaction
- a supervisor client holding the recovery capability may adopt
- any other client may **inspect** an orphan and see it reported in health and
  in `system.hello`, but gains no authority over it
- forced recovery remains available and is audited as an elevated operation

**Owed tests.** Owner disconnect; owner reconnect and adopt; adoption by a
supervisor; refusal of an unauthorized adopter; domain reload with a transaction
open; process restart with a transaction open; a stale transaction id after
either.
