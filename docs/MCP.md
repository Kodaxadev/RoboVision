# MCP adapter

RoboVision's editor protocol is transport-neutral. MCP is an adapter over that protocol, not the protocol itself.

Install the optional adapter:

```bash
pip install -e ".[mcp]"
```

Run the default stdio server:

```bash
robovision-mcp
```

The adapter exposes:

- `rv_status(host)` — calls `system.hello` and returns the live editor/version/revision/capability contract.
- `rv_tools(host, query, prefix, tags, include_schema, offset, limit)` — searches the connected host's live operation catalogue instead of a hard-coded list.
- `rv_method(host, method)` — returns the exact parameter schema and safety metadata for one method.
- `rv_call(host, method, params, if_revision)` — forwards one structured RoboVision operation.
- `rv_capture(host, params, if_revision)` — calls `viewport.capture`, returns its machine-readable provenance and sends the actual pixels as MCP image content.
- `rv_perception(host, params, model_passes)` — calls `perception.capture_bundle` and places the chosen passes into model context as labelled images.

The adapter targets the `mcp` 2.x server API, installed by the `mcp` extra.

Default local ports are Blender `9877` and Unity `9878`. Override them with `ROBOVISION_BLENDER_PORT` and `ROBOVISION_UNITY_PORT`.

## Why the MCP surface is small

RoboVision does not duplicate hundreds of editor methods into a second hard-coded tool registry. The editor host is authoritative and advertises its current methods through `system.hello`. This avoids version drift between an MCP wrapper and the editor implementation.

A future adapter may synthesize native MCP tools dynamically from host JSON Schemas, but those generated tools remain projections of the host contract rather than a second source of truth.

## Bundle sizing

`perception.capture_bundle` is priced per pixel: seven passes cost roughly 1.0s
at 512x384 and 8.6s at 1920x1080 on current Blender. Prefer a working size for
iteration and reserve full resolution for final evidence.

## Visual evidence

The full-resolution artifact remains on the editor machine. `rv_capture` sends a model-facing image copy through MCP. If the image is too large for conservative model-client limits, the adapter converts/downscales only that copy and reports the conversion metadata alongside the original artifact metadata.
