# RoboVision

RoboVision is a local-first control plane for AI-assisted 3D creation. The goal is not to give an agent a bigger script button; it is to give the agent reliable eyes, hands, state, history, and verification inside professional 3D editors.

The initial hosts are **Blender** and **Unity**. The protocol is editor-agnostic so future hosts can be added without weakening the contract.

## Non-negotiable design goals

- **Structured operations first.** Arbitrary host-code execution is an explicit, disabled-by-default escape hatch, never the normal control path.
- **Closed-loop perception.** Every meaningful edit can be followed by machine-readable inspection and visual evidence.
- **Editor-thread safety.** Host APIs execute only on the editor thread. The Blender add-on uses a non-blocking socket polled by `bpy.app.timers`; no socket worker thread touches `bpy`.
- **Stable identity.** Objects/assets get durable IDs. Topology elements are revision-scoped so stale vertex/edge/face indices are rejected.
- **Optimistic concurrency.** Mutations can include `if_revision` and fail if the scene changed since inspection.
- **Verified transactions.** Rollback is checked against a pre-transaction fingerprint; RoboVision never reports success when restoration cannot be proved.
- **Capability negotiation.** Clients discover what the connected host/version actually supports.
- **Evidence over confidence.** Results carry revision, timing, warnings, and evidence references where applicable.
- **Local by default.** Host bridges bind to loopback by default.

## Repository layout

```text
robovision/                 # editor-agnostic Python protocol + client
hosts/blender/              # Blender add-on
hosts/unity/                # Unity Editor package
docs/                       # architecture, protocol, gates and host contract
tests/                      # host-independent tests, plus the gates that drive a real editor
```

## Current target

RoboVision is being built foundation-first. The first acceptance target is **Gate 1: Observe → Mutate → Verify → Roll Back → Prove Restoration** in Blender. Tool-count expansion comes after the substrate is reliable.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), [`docs/PROTOCOL.md`](docs/PROTOCOL.md), and [`docs/GATES.md`](docs/GATES.md).
