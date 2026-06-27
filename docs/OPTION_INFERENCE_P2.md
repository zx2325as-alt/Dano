# Option Inference P2

P2 converts browser evidence into the typed `option_query` protocol introduced by P1.

## Deterministic boundary

P2 may activate a relation only when the captured evidence proves it:

- Search: a recent UI `fill` value occurs at one unique source-request token path.
- Cursor pagination: the next request cursor occurs at one stable path in the previous response.
- Page/offset pagination: request values follow a monotonic sequence and the request key has explicit pagination semantics.
- Exact validation: the submitted stable value occurs at one id-like source-request path and in the returned option record.
- Dependency: a source-request value maps to another recorded business field.

Every active relation contains confidence and evidence references. Relations below the auto threshold are not compiled.

## Safety properties

- No dotted-path parsing, templates, expressions or `eval`.
- Query/form bindings remain flat; nested mutation is JSON-only.
- Existing authored protocols are never overwritten.
- Recorded request implementation stays backend-only; public manifests expose capability flags only.
- P0 source security and P1 fail-closed validation remain the execution gates.

## Deferred

- Ambiguous low-confidence candidates and user confirmation UI.
- Signed option references bound to tenant, source and expiry.
- Cross-recording inference and legacy asset migration.
- Transaction IR version migration.
