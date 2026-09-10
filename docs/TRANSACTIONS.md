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
host-minted replacement goes only to the client that just proved itself.

### 4.2 The credential lifecycle belongs to the library

An agent must not have to reason about CSPRNG secrets, because a caller that has
to remember to generate and retain one will eventually not. `HostSession` owns
that lifecycle: `begin_transaction` generates the secret, keeps it privately and
sends only its verifier; `adopt_transaction` generates the *next* secret and
retains it before the call that rotates to it, so a lost adoption reply costs
nothing; `end_transaction` forgets the credential only once the transaction
actually ended, because a commit refused for contamination is still a
transaction that may need reclaiming. `recoverable_transaction` asks the host
what became of the work rather than assuming, and reports whether this session
still holds the credential — never the credential itself.

The MCP surface is `rv_transaction_begin`, `rv_transaction_status`,
`rv_transaction_adopt` and `rv_transaction_end`. The low-level `rv_call` remains
for expert use, and a caller that drives `transaction.begin` through it is
responsible for its own credential — which the response says, through
`recovery_precommitted`.

Secrets appear in no response, no diagnostic, no journal event, no operation
ledger record and no capability description. The gate asserts that no structured
output it collected contains one.

### 4.2.1 A lost acknowledgement must not cost authority

Precommitting the secret is necessary and was not sufficient. Two windows made
the guarantee untrue, both measured through the transport rather than inferred:

**The begin acknowledgement.** The secret was stored under the host-issued
transaction id, which only arrives in the reply. Lose the reply and the secret
was gone, leaving an orphan nobody could ever reclaim. The client now generates a
non-secret **correlation handle** alongside the secret and persists both *before*
sending; the host records the handle as non-authoritative metadata and reports it
on the transaction, so a reconnecting session recognises which transaction its
surviving secret belongs to. The handle grants nothing and the host id never
becomes client-authoritative.

**The adoption acknowledgement.** Adoption rotates, so a lost reply left the
client presenting the old secret while the host expected the new one — and no way
to tell whether the rotation had happened. The host now counts a non-secret
**recovery generation**, incremented when it installs a new verifier and reported
in transaction state. The client keeps both credentials and reads the counter:
unchanged means the rotation never happened and the original is still current;
advanced means it did and the replacement — already in hand — is. An ambiguous
acknowledgement becomes a deterministic lookup rather than a guess.

Neither mechanism authenticates. The handle identifies a request, the generation
identifies which credential is current, and only the secret proves authority.

Reconciliation matches the generation **exactly**, and there are only two
legitimate answers: unchanged means the rotation did not happen and the current
secret still is current; equal to the pending generation means it did. Anything
else — a host further ahead than this session ever pended, or behind where it
last knew the host to be — means the two credential histories have diverged, and
is reported as `CREDENTIAL_STATE_DIVERGED` rather than guessed at. An inequality
would have promoted there, handing over a secret whose verifier the host does not
hold.

**Terminal replies** are resolved by reading. `transaction.status` reports the
open transaction or the finished record with its proof, so a caller whose commit
acknowledgement was lost learns the outcome instead of re-sending the operation
to discover it. That is reachable from the public surface: `rv_transaction_status`
without an id discovers and reconciles this session's recoverable transaction,
and with one resolves that transaction open or finished.

Both hosts implement this, and the Unity port is where it earns its keep. Unity
destroys and rebuilds the bridge on a domain reload, at a moment nobody chose,
while the editing world survives — so the window a host-minted credential loses
outright is not a rare accident there, it is a routine editor event.
`tools/unity-ack-loss.sh` drives all seven windows against a real editor using
the public `HostSession` unchanged, which is also the test of whether the public
client is genuinely host-agnostic. The Blender equivalent is
`tests/blender/ack_loss.py`; the evidence is kept apart rather than merged.

All credential transitions run under the session's lock, so no second thread can
observe a half-transition: create-pending-then-send-then-bind for begin, and
create-next-then-send-then-promote for adoption, are each one serialized step.

**Scope, honestly.** The credential store survives socket loss, not the death of
the session process itself. A process death may therefore leave an orphan that
can only be discarded. That is acceptable for this milestone and is not hidden: a
durable credential facility is future work, and no OS keychain or encrypted store
is introduced now.

### 4.3 One logical controller per host session

The MCP adapter keeps one connection per host, which is correct for a single
autonomous controller and is the assumption the Artist Loop is built on. It also
means that several logical agents sharing one MCP server process share one
connection identity, and therefore one transaction ownership.

That is not solved here and does not need to be. What matters is that the
boundary is real rather than accidental: **independently owned transactions
require distinct host sessions**, because ownership is the connection and the
host counts nothing else. A future planner or sub-agent architecture that needs
agents to hold independent authority must give them separate sessions rather than
sharing one and distinguishing them in request parameters — a client-supplied
identity would be a claim, not a credential. Every refusal is
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
