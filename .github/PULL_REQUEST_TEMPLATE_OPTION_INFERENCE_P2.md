## Option Inference P2 review checklist

- [ ] Every active inferred relation has evidence references and confidence at or above the auto threshold.
- [ ] Existing authored `option_query` contracts are preserved unchanged.
- [ ] No free-form expression, dotted-path parser or `eval` was added.
- [ ] Dynamic source execution still passes through the P0 security and P1 validation gates.
- [ ] Source request internals are not projected into public manifests.
- [ ] Ambiguous evidence fails closed instead of choosing a candidate.
- [ ] Transaction IR and compiled `api_request` contain equivalent query contracts.
- [ ] P0, P1, P2 and request-capture regression tests pass.
