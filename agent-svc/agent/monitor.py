"""Monitor module — scheduled change detection for web pages.

Architecture:
- Ofelia (scheduler container) runs `python3 -m agent.monitor check_all` every N minutes
- check_all reads monitor configs from Valkey, scrapes each URL, diffs, notifies
- Results stored in Valkey under monitor:{id}:history
"""

import asyncio
import difflib
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

import httpx
from redis import Redis

logger = logging.getLogger(__name__)

REDIS_URL = "redis://valkey:6379/0"
MONITOR_KEY = "monitors"  # hash: monitor_id -> json config
HISTORY_KEY = "monitor:{}:history"  # list of check results


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_redis() -> Redis:
    return Redis.from_url(REDIS_URL, decode_responses=True)


def get_all_monitors() -> dict[str, dict]:
    """Get all monitor configs from Valkey."""
    r = _get_redis()
    raw = r.hgetall(MONITOR_KEY)
    monitors = {}
    for mid, val in raw.items():
        try:
            monitors[mid] = json.loads(val)
        except json.JSONDecodeError:
            continue
    return monitors


def save_monitor(monitor_id: str, config: dict) -> None:
    """Save or update a monitor config."""
    r = _get_redis()
    r.hset(MONITOR_KEY, monitor_id, json.dumps(config))


def delete_monitor(monitor_id: str) -> None:
    """Delete a monitor config and its history."""
    r = _get_redis()
    r.hdel(MONITOR_KEY, monitor_id)
    r.delete(HISTORY_KEY.format(monitor_id))


def get_monitor(monitor_id: str) -> dict | None:
    """Get a single monitor config."""
    r = _get_redis()
    raw = r.hget(MONITOR_KEY, monitor_id)
    if raw is None:
        return None
    return json.loads(raw)


def _compute_diff(old: str, new: str) -> str:
    """Compute a unified diff between old and new content."""
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    diff = list(difflib.unified_diff(old_lines, new_lines, fromfile="previous", tofile="current", n=3))
    return "".join(diff[:100])  # cap at 100 lines


def _store_check(monitor_id: str, result: dict) -> None:
    """Store a check result in the history list."""
    r = _get_redis()
    key = HISTORY_KEY.format(monitor_id)
    r.lpush(key, json.dumps(result))
    r.ltrim(key, 0, 49)  # keep last 50 checks


async def check_monitor(monitor_id: str, config: dict) -> dict:
    """Run a single monitor check: scrape → diff → notify."""
    url = config["url"]
    scraper_url = config.get("scraper_url", "http://scraper-svc:8001")
    webhook_url = config.get("webhook")
    previous_content = config.get("last_content", "")

    result: dict[str, Any] = {
        "monitor_id": monitor_id,
        "url": url,
        "checked_at": _now_iso(),
        "changed": False,
        "diff": "",
    }

    # Scrape
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(f"{scraper_url}/scrape", json={"url": url})
            if resp.status_code != 200:
                result["error"] = f"Scrape returned {resp.status_code}"
                _store_check(monitor_id, result)
                return result
            body = resp.json()
            if not body.get("success"):
                result["error"] = body.get("error", "Scrape failed")
                _store_check(monitor_id, result)
                return result
            current_content = body.get("data", {}).get("markdown", "")
    except Exception as e:
        result["error"] = f"Scrape error: {e}"
        _store_check(monitor_id, result)
        return result

    # Compare
    if previous_content and current_content != previous_content:
        diff = _compute_diff(previous_content, current_content)
        result["changed"] = True
        result["diff"] = diff
        result["previous_length"] = len(previous_content)
        result["current_length"] = len(current_content)

    # Update stored content
    config["last_content"] = current_content
    config["last_checked"] = _now_iso()
    config["last_result"] = result.get("changed", False) and "changed" or "unchanged"
    save_monitor(monitor_id, config)

    # Notify via webhook
    if webhook_url and result.get("changed"):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(webhook_url, json={
                    "event": "monitor.changed",
                    "monitor_id": monitor_id,
                    "url": url,
                    "diff": result["diff"],
                    "checked_at": result["checked_at"],
                })
        except Exception as e:
            logger.warning("Webhook delivery failed for monitor %s: %s", monitor_id, e)

    _store_check(monitor_id, result)
    return result


async def check_all_async() -> list[dict]:
    """Check all monitors."""
    monitors = get_all_monitors()
    results = []
    for mid, config in monitors.items():
        logger.info("Checking monitor %s: %s", mid, config.get("url"))
        try:
            result = await check_monitor(mid, config)
            results.append(result)
            if result.get("changed"):
                logger.info("Monitor %s: CHANGED", mid)
        except Exception as e:
            logger.error("Monitor %s failed: %s", mid, e)
    return results


def check_all() -> None:
    """Synchronous entrypoint for Ofelia scheduler."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    results = asyncio.run(check_all_async())
    changed = [r for r in results if r.get("changed")]
    if changed:
        logger.info(
            "Monitors checked: %d, changed: %d",
            len(results),
            len(changed),
        )
        for r in changed:
            logger.info("  CHANGED: %s (%s)", r["url"], r["monitor_id"])
    else:
        logger.info("Monitors checked: %d, all unchanged", len(results))


if __name__ == "__main__":
    check_all()
