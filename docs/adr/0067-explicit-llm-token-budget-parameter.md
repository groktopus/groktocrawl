# ADR-0067: Select the LLM Token Budget Parameter Explicitly

- **Status:** accepted
- **Date:** 2026-09-27

## Context

OpenAI-compatible providers differ on whether chat completion requests accept
`max_tokens` or `max_completion_tokens`. Sending the unsupported field causes
the whole request to fail. Model names are not a reliable signal because
providers expose compatible models through different APIs, and deployments may
override the model per request.

## Decision

The agent service exposes `LLM_TOKEN_PARAMETER`, explicitly selecting either
`max_tokens` or `max_completion_tokens`. It sends only the selected field for
regular, streaming, structured, and health requests. `max_tokens` remains the
default for backward compatibility. The legacy visible-output budget defaults
to 8192; the completion budget defaults to 32768 and counts reasoning plus
visible output. A separate positive health budget defaults to 1 and uses the
selected field. Settings are validated as positive integers.

There is no model-name heuristic or automatic budget conversion. Per-request
model overrides inherit the deployment's selected parameter and must be
compatible with it. The small health request establishes endpoint reachability,
not that a useful response fits within the health budget.

## Considered Options

- **Always send `max_tokens`:** preserves existing behavior but fails with
  providers that reject this field.
- **Infer the field from the model name:** rejected because provider routing and
  per-request model overrides make names unreliable.
- **Select the field through settings (chosen):** explicit, validated, and
  consistently applied across all LLM request paths.

## Consequences

Operators can select the request field required by their provider while keeping
existing deployments on the legacy behavior. Deployments serving model families
that require different fields need separate configurations. Completion budgets
must be chosen with the provider's total-token semantics in mind. Detecting
`finish_reason: "length"` in streaming responses is outside this decision; the
current streaming path may report normal completion in that case and should be
addressed separately.

## Links

- [ADR-0031](0031-settings-object.md)
