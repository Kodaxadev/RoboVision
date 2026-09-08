# RoboVision protocol 1.0

The host transport is newline-delimited UTF-8 JSON over loopback TCP by default. Each line is one request or response. The protocol layer is independent of MCP.

## Request

```json
{"rv":"1.0","id":"uuid","method":"object.transform","params":{"object":"b3d:...","location":[1,2,3]},"if_revision":41}
```

`if_revision` is optional for reads and strongly recommended for mutations.

## Success

```json
{"rv":"1.0","id":"uuid","ok":true,"revision":42,"result":{},"timing_ms":1.72}
```

## Failure

```json
{"rv":"1.0","id":"uuid","ok":false,"revision":42,"error":{"code":"STALE_REVISION","message":"scene changed","retryable":true,"data":{"expected":41,"actual":42}}}
```

## Core error codes

- `INVALID_REQUEST`
- `PROTOCOL_MISMATCH`
- `UNKNOWN_METHOD`
- `INVALID_PARAMS`
- `NOT_FOUND`
- `UNSUPPORTED`
- `STALE_REVISION`
- `STALE_TOPOLOGY`
- `INVALID_CONTEXT`
- `TRANSACTION_ACTIVE`
- `NO_TRANSACTION`
- `ROLLBACK_INCOMPLETE`
- `HOST_EXCEPTION`

## `system.hello`

Every host returns:

- protocol version
- RoboVision host implementation/version
- editor name/version
- current scene revision
- enabled capabilities
- transport/security facts
- limitations/warnings relevant to this editor/version

## Method metadata

Capabilities list methods with at least:

```json
{"name":"mesh.bevel","mutating":true,"evidence":false,"requires_ui":false,"stability":"alpha"}
```

Clients must not infer support from a method existing on another host.

## Evidence references

Visual operations return an `artifact` record containing a local path initially. A transport-neutral artifact service will later let MCP and remote adapters convert this into native image/resource forms without changing the host operation contract.
