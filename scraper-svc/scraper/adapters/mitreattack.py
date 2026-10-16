"""
MITRE ATT&CK adapter — technique, software, and group information from the ATT&CK framework.

Fetches structured STIX data from the MITRE CTI GitHub repository.
No API key required.

API docs: https://attack.mitre.org/resources/working-with-attack/
"""

from __future__ import annotations

import logging
import re

from ..source_http import source_httpx_client
from ._helpers import scrape_page
from .base import AdapterContext, AdapterError, AdapterResult, SiteAdapter, adapter

logger = logging.getLogger(__name__)

_MITRE_URL_PATTERNS = [
    re.compile(r"^https?://attack\.mitre\.org/techniques/[Tt]\d{4,}"),
    re.compile(r"^https?://attack\.mitre\.org/software/[Ss]\d{4,}"),
    re.compile(r"^https?://attack\.mitre\.org/groups/[Gg]\d{4,}"),
    re.compile(r"^https?://attack\.mitre\.org/mitigations/[Mm]\d{4,}"),
    re.compile(r"^https?://attack\.mitre\.org/tactics/[Tt][Aa]\d{4,}"),
]

_STIX_BASE = "https://raw.githubusercontent.com/mitre/cti/master"

_STIX_MAP = {
    "techniques": f"{_STIX_BASE}/enterprise-attack/attack-pattern/attack-pattern--",
    "software": f"{_STIX_BASE}/enterprise-attack/software/software--",
    "groups": f"{_STIX_BASE}/enterprise-attack/intrusion-set/intrusion-set--",
    "mitigations": f"{_STIX_BASE}/enterprise-attack/course-of-action/course-of-action--",
    "tactics": f"{_STIX_BASE}/enterprise-attack/x-mitre-tactic/x-mitre-tactic--",
}

_ATTACK_TYPE = re.compile(r"/techniques/|/software/|/groups/|/mitigations/|/tactics/")
_OBJ_ID = re.compile(r"([TtSsGgMm][Aa]?\d{4,})")


def _parse_attack_url(url: str) -> tuple[str, str] | None:
    """Return (type, object_id) or None."""
    type_match = _ATTACK_TYPE.search(url)
    id_match = _OBJ_ID.search(url)
    if type_match and id_match:
        raw_type = type_match.group(0).strip("/")
        return raw_type, id_match.group(1)
    return None


async def _fetch_stix_data(obj_type: str, obj_id: str) -> dict | None:
    """Walk MITRE CTI STIX bundles to find the matching object.

    Since objects are organized by UUID in individual files, we search
    the JSON for the matching object by the ATT&CK ID.
    """
    stix_key = {
        "techniques": "attack-pattern",
        "software": "software",
        "groups": "intrusion-set",
        "mitigations": "course-of-action",
        "tactics": "x-mitre-tactic",
    }.get(obj_type)
    if not stix_key:
        return None

    # Fetch the bundle index file
    bundle_path = {
        "techniques": f"{_STIX_BASE}/enterprise-attack/attack-pattern/attack-pattern.json",
        "software": f"{_STIX_BASE}/enterprise-attack/software/software.json",
        "groups": f"{_STIX_BASE}/enterprise-attack/intrusion-set/intrusion-set.json",
        "mitigations": f"{_STIX_BASE}/enterprise-attack/course-of-action/course-of-action.json",
        "tactics": f"{_STIX_BASE}/enterprise-attack/x-mitre-tactic/x-mitre-tactic.json",
    }.get(obj_type)

    if not bundle_path:
        return None

    try:
        async with source_httpx_client(timeout=15) as client:
            resp = await client.get(bundle_path)
            if resp.status_code != 200:
                return None
            bundle = resp.json()
            for obj in bundle.get("objects", []):
                if obj.get("id"):
                    # Match by external reference
                    for ext_ref in obj.get("external_references", []):
                        if ext_ref.get("external_id", "").upper() == obj_id.upper():
                            return obj
    except Exception as exc:
        logger.debug("STIX fetch failed for %s %s: %s", obj_type, obj_id, exc)
    return None


def _format_stix_result(obj: dict, obj_type: str, obj_id: str) -> tuple[str, dict]:
    name = obj.get("name", "")
    description = obj.get("description", "")
    ext_refs = obj.get("external_references", [])
    x_mitre_platforms = obj.get("x_mitre_platforms", [])
    x_mitre_detection = obj.get("x_mitre_detection", "")
    kill_chain = obj.get("kill_chain_phases", [])
    aliases = obj.get("aliases", []) if obj_type == "groups" else []

    metadata = {
        "attack_id": obj_id,
        "type": obj_type,
        "name": name,
        "platforms": ", ".join(x_mitre_platforms) if x_mitre_platforms else "",
        "source": "mitre-stix",
    }

    parts = [f"## {name} ({obj_id})", ""]
    parts.append(f"**Type:** {obj_type.capitalize()}  ")
    if x_mitre_platforms:
        parts.append(f"**Platforms:** {', '.join(x_mitre_platforms)}  ")
    if kill_chain:
        tactics = [kcp.get("phase_name", "") for kcp in kill_chain]
        parts.append(f"**Tactics:** {', '.join(tactics)}  ")
    if aliases:
        parts.append(f"**Aliases:** {', '.join(aliases)}  ")
    parts.append("")

    if description:
        parts.append(description)
        parts.append("")

    if x_mitre_detection:
        parts.append("### Detection")
        parts.append("")
        parts.append(x_mitre_detection)
        parts.append("")

    if ext_refs:
        parts.append("### References")
        parts.append("")
        for ref in ext_refs[:10]:
            url = ref.get("url", "")
            src = ref.get("source_name", "")
            if url:
                parts.append(f"- [{src}]({url})" if src else f"- {url}")
        parts.append("")

    return "\n".join(parts), metadata


@adapter
class MitreAttackAdapter(SiteAdapter):
    name = "mitreattack"
    patterns = _MITRE_URL_PATTERNS
    priority = 200

    async def scrape(self, url: str, ctx: AdapterContext) -> AdapterResult:
        parsed = _parse_attack_url(url)
        if not parsed:
            raise AdapterError(f"Could not parse ATT&CK URL: {url}")
        obj_type, obj_id = parsed

        obj = await ctx.with_timeout(_fetch_stix_data(obj_type, obj_id), timeout=15)
        if obj:
            md, meta = _format_stix_result(obj, obj_type, obj_id)
            return AdapterResult(
                success=True, markdown=md, metadata=meta, source="mitre-stix", url=url
            )

        result = await scrape_page(url)
        if result:
            return AdapterResult(
                success=True,
                markdown=result,
                metadata={"attack_id": obj_id, "source": "mitre-html"},
                url=url,
            )

        raise AdapterError(f"Could not extract ATT&CK data for {obj_id}")
