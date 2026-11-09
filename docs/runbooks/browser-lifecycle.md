# Browser lifecycle and admission

`browser-svc` owns each session before the first Playwright startup await. A reservation covers creation, active use, and cleanup, not only visible sessions.

- At most one session starts at a time; a second simultaneous creation fails promptly with HTTP 503 instead of accumulating a queue.
- At most two sessions may be creating, active, or closing. This conservative ceiling targets the standard 256-task container envelope. Revisit it only with measured browser-process demand and regression/load testing, not by raising the PID limit to mask leaks.
- Creation has a 20-second overall deadline and returns HTTP 504 on timeout. Failure/cancellation starts independently owned cleanup, including resources acquired before registration.
- Cleanup attempts page, context, browser, and controller independently, with at most two seconds per operation. Repeated request cancellation cannot cancel the cleanup owner. Shutdown stops admission and retires both pending and active sessions.
- Playwright startup is retained separately until its controller becomes available. A late controller is stopped, rather than calling the context-manager exit before its subprocess transport exists.

`GET /health` is a cheap admission/readiness check; it never launches a browser. It includes `admission.creating`, `closing`, `quarantined`, `reserved`, and `limit`. HTTP 503 means there is no current admission capacity, shutdown is underway, or cleanup is uncertain. Existing sessions remain executable when admission is full. Unknown cgroup capacity remains explicitly `unknown`; admission availability is not a guarantee that an OS-level launch will succeed.

A new browser previously increased measured container tasks from 7 to 98. Admission therefore requires 100 tasks of headroom rather than the former reserve of 16. The container's PID limit remains unchanged.

## Incomplete cleanup

If a dependency ignores cancellation or resource closure fails, the owner remains quarantined and admission fails closed with `cleanup_incomplete`. This intentionally does not claim that uncertain resources have been reclaimed. Late acquired handles are still closed. Inspect the sanitized health counts, container task count, and service logs; after checking active work, restart only browser-svc using the deployment's canonical Compose configuration. Do not kill unrelated host processes or recreate the stack.

Verify recovery with a temporary browser, execute a script, retrieve content, delete it, and compare task counts with the pre-session baseline. An empty `/browsers` list alone is insufficient evidence. A restart can interrupt live browser sessions, but must not change persistent stores or other service containers.

## Rollback

Record the current image ID and canonical Compose checksum before deployment. Retain the previous image. Change only browser-svc's image/revision in the canonical deployment, then recreate only that service with `docker compose up -d --no-deps --force-recreate browser-svc`. On a failed create/execute/delete check or unexpected task growth, restore that service's previous image/revision and repeat the same scoped command. No data/schema migration is involved.
