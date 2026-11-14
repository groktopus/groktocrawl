# Browser recovery 722: change contract and specification

Owner request: troubleshoot unhealthy browser service, then file issue, update
ADRs, implement reviewed fixes, merge and deploy production. Authority includes
those actions, not unrelated services/security-policy changes. Full delivery
path: cross-service deadline and container recovery boundary.

Acceptance:
1. Browser fallback transport outlives 45-second navigation and bounded cleanup.
2. Cleanup failure records stage/outcome safely; preserve uncertain ownership.
3. Opt-in dedicated container exits 75 after one 30-second grace; disabled or
   unsupported hosts cannot recycle, normal shutdown fences scheduling.
4. No browser action replay, no production fault injection, no resource-limit or
   source-network relaxation. Other services, including PR 721 MCP, remain intact.
5. Reviewed exact-head PR passes required gates; production serves a complete
   browser lifecycle with zero reserved sessions after delete.

Non-goals: prove the unlogged historical exception, eliminate all Chromium
failures, redesign browser isolation, or promise uninterrupted sessions.

Design: ADR 0080. Independent reviewer conditionally approved the design with
shutdown-fencing and container-boundary tests required before merge.
