"""Inspect the protected renderer from inside its own container namespace."""

from __future__ import annotations

import shlex
import subprocess
from collections import Counter
from pathlib import Path

NET_ADMIN = 1 << 12
GATEWAY = "172.31.254.10"
GATEWAY_PORT = "8080"


def _status() -> dict[str, str]:
    rows = {}
    for line in Path("/proc/1/status").read_text(encoding="ascii").splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            rows[key] = value.strip()
    return rows


def _rules(command: str) -> list[str]:
    return subprocess.check_output([command, "-S"], text=True).splitlines()


def _normalized_rule(row: str) -> tuple[str, ...]:
    """Normalize only iptables' equivalent option ordering and TCP shorthand."""
    try:
        tokens = shlex.split(row)
    except ValueError:
        return ("<invalid-rule>", row)
    if len(tokens) < 2 or tokens[0] != "-A" or (len(tokens) - 2) % 2:
        return tuple(tokens)
    chain = tokens[1]
    options = tokens[2:]
    pairs = list(zip(options[::2], options[1::2], strict=True))
    names = [name for name, _ in pairs]
    if len(set(names)) != len(names):
        return tuple(tokens)
    values = dict(pairs)
    if values.get("-j") != "ACCEPT":
        return tuple(tokens)

    if set(values) == {"-m", "--ctstate", "-j"} and values["-m"] == "conntrack":
        states = values["--ctstate"].split(",")
        if (
            len(states) == 2
            and set(states) == {"ESTABLISHED", "RELATED"}
            and chain in {"INPUT", "OUTPUT"}
        ):
            values["--ctstate"] = ",".join(sorted(states))
            return (
                "-A",
                chain,
                *(token for pair in sorted(values.items()) for token in pair),
            )

    allowed_gateway_keys = {"-p", "-d", "--dport", "-j"}
    if set(values) == allowed_gateway_keys or set(values) == allowed_gateway_keys | {
        "-m"
    }:
        if (
            chain == "OUTPUT"
            and values.get("-p") == "tcp"
            and values.get("-d") == f"{GATEWAY}/32"
            and values.get("--dport") == GATEWAY_PORT
            and values.get("-j") == "ACCEPT"
            and values.get("-m", "tcp") == "tcp"
        ):
            values.pop("-m", None)
            return (
                "-A",
                chain,
                *(token for pair in sorted(values.items()) for token in pair),
            )
    return tuple(tokens)


def validate_firewall_rules(rules: list[str], *, ipv6: bool) -> None:
    """Require exactly the intended default-deny policy and allow rules."""
    expected_policies = {
        "-P INPUT DROP",
        "-P FORWARD DROP",
        "-P OUTPUT DROP",
    }
    policies = {row for row in rules if row.startswith("-P ")}
    if policies != expected_policies:
        version = "IPv6" if ipv6 else "IPv4"
        raise RuntimeError(f"renderer {version} chain policies are incomplete")

    established = (
        "-m",
        "conntrack",
        "--ctstate",
        "ESTABLISHED,RELATED",
        "-j",
        "ACCEPT",
    )
    expected_rules = [
        ("-A", "INPUT", *established),
        ("-A", "OUTPUT", *established),
    ]
    if not ipv6:
        expected_rules.append(
            (
                "-A",
                "OUTPUT",
                "-p",
                "tcp",
                "-d",
                f"{GATEWAY}/32",
                "--dport",
                GATEWAY_PORT,
                "-j",
                "ACCEPT",
            )
        )
    observed_rules = [_normalized_rule(row) for row in rules if row.startswith("-A ")]
    expected = Counter(_normalized_rule(" ".join(rule)) for rule in expected_rules)
    if Counter(observed_rules) != expected:
        version = "IPv6" if ipv6 else "IPv4"
        raise RuntimeError(f"renderer {version} firewall allow rules are unexpected")
    if len(policies) + len(observed_rules) != len(rules):
        version = "IPv6" if ipv6 else "IPv4"
        raise RuntimeError(f"renderer {version} firewall contains unexpected rules")


def main() -> None:
    status = _status()
    if int(status["Uid"].split()[0]) != 10001:
        raise RuntimeError("renderer process did not drop its user identity")
    if int(status["CapEff"], 16) & NET_ADMIN or int(status["CapBnd"], 16) & NET_ADMIN:
        raise RuntimeError("renderer retained network administration capability")
    if status.get("NoNewPrivs") != "1":
        raise RuntimeError("renderer privilege escalation is not disabled")

    ipv4 = _rules("iptables")
    validate_firewall_rules(ipv4, ipv6=False)

    ipv6 = _rules("ip6tables")
    validate_firewall_rules(ipv6, ipv6=True)

    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        rows = Path(table).read_text(encoding="ascii").splitlines()[1:]
        api_port = f":{8012:04X}"
        if any(
            row.split()[3] == "0A" and row.split()[1].endswith(api_port) for row in rows
        ):
            raise RuntimeError("renderer unexpectedly listens on the browser API port")
    print("protected_renderer_isolation=pass")


if __name__ == "__main__":
    main()
