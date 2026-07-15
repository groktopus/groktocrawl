"""Bounded, frame-aware CAPTCHA recovery helpers."""

import base64
import json
import logging
import math

import httpx

from common.metrics import METRICS

from .barrier import BarrierInfo, _classify_barrier
from .settings import load_settings

logger = logging.getLogger(__name__)
MAX_IMAGE_GRID_ROUNDS = 2
# ponytail: process-wide circuit breaker; add TTL state if live reconfiguration is needed.
_vision_unavailable = False
_settings = load_settings()
_captcha_counter = METRICS.counter(
    "captcha_attempts_total",
    "CAPTCHA recovery attempts",
    ["provider", "strategy", "outcome"],
)

_SELECTORS = {
    "recaptcha": {
        "frame": "google.com/recaptcha",
        "checkbox": "#recaptcha-anchor",
        "grid": "#rc-imageselect",
        "tiles": ".rc-imageselect-tile",
        "prompt": ".rc-imageselect-desc-wrapper",
        "submit": "#recaptcha-verify-button",
        "response": "textarea[name='g-recaptcha-response'], #g-recaptcha-response",
    },
    "hcaptcha": {
        "frame": "hcaptcha.com",
        "checkbox": "#checkbox, [role='checkbox']",
        "grid": ".challenge-container",
        "tiles": ".task-image",
        "prompt": ".prompt-text",
        "submit": ".button-submit, button[type='submit']",
        "response": "textarea[name='h-captcha-response'], #h-captcha-response",
    },
    "turnstile": {
        "frame": "challenges.cloudflare.com/turnstile",
        "checkbox": "input[type='checkbox'], [role='checkbox']",
        "grid": ".challenge-container",
        "tiles": ".task-image",
        "prompt": ".prompt-text",
        "submit": "button[type='submit']",
        "response": "input[name='cf-turnstile-response'], textarea[name='cf-turnstile-response']",
    },
}


def _record_attempt(provider: str, strategy: str, outcome: str) -> None:
    _captcha_counter.inc(
        {"provider": provider, "strategy": strategy, "outcome": outcome}
    )


def parse_tile_response(content: str, tile_count: int) -> list[int] | None:
    """Accept only strict, zero-based, unique row-major tile selections."""
    try:
        data = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return None
    tiles = (
        data.get("tiles")
        if isinstance(data, dict) and data.get("submit") is True
        else None
    )
    if not isinstance(tiles, list) or any(type(tile) is not int for tile in tiles):
        return None
    if len(set(tiles)) != len(tiles) or any(
        tile < 0 or tile >= tile_count for tile in tiles
    ):
        return None
    return tiles


async def _vision_request(
    prompt: str, image: bytes, tile_count: int
) -> tuple[list[int] | None, str]:
    global _vision_unavailable
    base_url = _settings.captcha_vision_base_url
    api_key = _settings.captcha_vision_api_key
    model = _settings.captcha_vision_model
    if _vision_unavailable or not (base_url and api_key and model):
        return None, "unavailable"
    columns = int(math.sqrt(tile_count))
    rows = tile_count // columns if columns and tile_count % columns == 0 else 1
    instruction = (
        f"{prompt}\nGrid: {rows} rows x {columns if rows > 1 else tile_count} columns; "
        "use zero-based row-major tile indices. Return JSON "
        '{"tiles":[...],"submit":true} only.'
    )
    try:
        async with httpx.AsyncClient(
            timeout=_settings.captcha_vision_timeout
        ) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": instruction},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": "data:image/png;base64,"
                                        + base64.b64encode(image).decode("ascii")
                                    },
                                },
                            ],
                        }
                    ],
                    "response_format": {"type": "json_object"},
                },
            )
        if response.status_code != 200:
            if response.status_code in {400, 401, 403, 404, 422}:
                _vision_unavailable = True
                return None, "unavailable"
            return None, "failure"
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (KeyError, TypeError, ValueError):
            logger.warning("Vision API returned a malformed success response")
            return None, "failure"
        tiles = parse_tile_response(content, tile_count)
        return tiles, "success" if tiles is not None else "failure"
    except httpx.HTTPError:
        return None, "failure"


async def ask_vision_for_tiles(
    prompt: str, image: bytes, tile_count: int
) -> list[int] | None:
    """Request tile indices while preserving a simple testable public helper."""
    tiles, _outcome = await _vision_request(prompt, image, tile_count)
    return tiles


async def _provider_frames(page, provider: str):
    """Return provider URL frames first, then selector-matching fixture frames."""
    selectors = _SELECTORS[provider]
    main_frame = getattr(page, "main_frame", None)
    frames = [frame for frame in page.frames if frame is not main_frame]
    needle = selectors["frame"]
    matched = [frame for frame in frames if needle in getattr(frame, "url", "").lower()]

    for frame in frames:
        if frame in matched:
            continue
        try:
            has_checkbox = await frame.locator(selectors["checkbox"]).count() > 0
            grid = frame.locator(selectors["grid"]).first
            has_grid = await grid.locator(selectors["tiles"]).count() > 0
            if has_checkbox or has_grid:
                matched.append(frame)
        except Exception as exc:
            logger.debug("CAPTCHA frame probe failed for %s: %s", provider, exc)
            continue
    return matched


async def _is_solved(page, provider: str, frames) -> bool:
    selectors = _SELECTORS.get(provider, {})
    try:
        response = page.locator(selectors["response"])
        if await response.count() and (await response.input_value()).strip():
            return True
    except Exception:
        pass
    for frame in frames:
        try:
            checkbox = frame.locator(selectors["checkbox"]).first
            if await checkbox.count() == 0:
                continue
            if await checkbox.get_attribute("aria-checked") == "true":
                return True
            if await checkbox.is_checked():
                return True
        except Exception:
            pass
    return False


async def _grid_frame(frames, selectors):
    """Return the first provider frame that exposes a usable tile grid."""
    for frame in frames:
        try:
            grid = frame.locator(selectors["grid"]).first
            if await grid.locator(selectors["tiles"]).count() > 0:
                return frame
        except Exception as exc:
            logger.debug("CAPTCHA grid probe failed: %s", exc)
            continue
    return None


async def resolve_captcha(page, url: str) -> tuple[BarrierInfo | None, list[str]]:
    """Recover in provider frames; success requires a token or checked widget."""
    html = await page.content()
    barrier = _classify_barrier("", page.url, "", html)
    if barrier.barrier_type != "captcha":
        return None, []
    provider = barrier.provider or "generic"
    selectors = _SELECTORS.get(provider)
    if not selectors:
        return barrier, []
    attempts: list[str] = ["passive_wait"]
    await page.wait_for_timeout(1000)
    frames = await _provider_frames(page, provider)
    if await _is_solved(page, provider, frames):
        _record_attempt(provider, "passive_wait", "success")
        return None, attempts
    _record_attempt(provider, "passive_wait", "failure")

    attempts.append("checkbox")
    clicked = False
    for frame in frames:
        try:
            await frame.locator(selectors["checkbox"]).first.click(timeout=2000)
            clicked = True
            break
        except Exception:
            continue
    if clicked:
        await page.wait_for_timeout(1000)
    frames = await _provider_frames(page, provider)
    if await _is_solved(page, provider, frames):
        _record_attempt(provider, "checkbox", "success")
        return None, attempts
    _record_attempt(provider, "checkbox", "failure")

    for _round in range(MAX_IMAGE_GRID_ROUNDS):
        attempts.append("vision_grid")
        frames = await _provider_frames(page, provider)
        frame = await _grid_frame(frames, selectors)
        if frame is None:
            _record_attempt(provider, "vision_grid", "unavailable")
            break
        try:
            grid = frame.locator(selectors["grid"]).first
            tiles = grid.locator(selectors["tiles"])
            count = await tiles.count()
            if count <= 0:
                _record_attempt(provider, "vision_grid", "unavailable")
                break
            prompt = await grid.locator(selectors["prompt"]).first.inner_text()
            selected, outcome = await _vision_request(
                prompt, await grid.screenshot(), count
            )
            if selected is None:
                _record_attempt(provider, "vision_grid", outcome)
                if outcome == "failure":
                    continue
                break
            for index in selected:
                await tiles.nth(index).click()
            await grid.locator(selectors["submit"]).first.click(timeout=2000)
            await page.wait_for_timeout(1000)
            if await _is_solved(page, provider, frames):
                _record_attempt(provider, "vision_grid", "success")
                return None, attempts
            _record_attempt(provider, "vision_grid", "failure")
        except Exception:
            _record_attempt(provider, "vision_grid", "failure")
            break
    return barrier, attempts
