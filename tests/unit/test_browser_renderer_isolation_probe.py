"""Synthetic firewall-output contracts for the Docker qualification probe."""

import pytest

from tests.integration.browser_renderer_isolation_probe import (
    GATEWAY,
    GATEWAY_PORT,
    validate_firewall_rules,
)


def _ipv4_rules() -> list[str]:
    return [
        "-P INPUT DROP",
        "-P FORWARD DROP",
        "-P OUTPUT DROP",
        "-A INPUT -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT",
        "-A OUTPUT -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT",
        f"-A OUTPUT -p tcp -d {GATEWAY}/32 --dport {GATEWAY_PORT} -j ACCEPT",
    ]


def _ipv6_rules() -> list[str]:
    return [
        "-P INPUT DROP",
        "-P FORWARD DROP",
        "-P OUTPUT DROP",
        "-A INPUT -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT",
        "-A OUTPUT -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT",
    ]


def test_probe_accepts_exact_protected_ipv4_and_ipv6_policies():
    validate_firewall_rules(_ipv4_rules(), ipv6=False)
    validate_firewall_rules(_ipv6_rules(), ipv6=True)


def test_probe_rejects_extra_ipv4_accept_rule():
    rules = _ipv4_rules()
    rules.append("-A OUTPUT -d 203.0.113.9/32 -j ACCEPT")
    with pytest.raises(RuntimeError, match="IPv4 firewall allow rules are unexpected"):
        validate_firewall_rules(rules, ipv6=False)


def test_probe_rejects_forward_policy_or_unknown_rule():
    rules = _ipv4_rules()
    rules[1] = "-P FORWARD ACCEPT"
    with pytest.raises(RuntimeError, match="IPv4 chain policies are incomplete"):
        validate_firewall_rules(rules, ipv6=False)

    rules = _ipv4_rules()
    rules.append("-N UNEXPECTED_CHAIN")
    with pytest.raises(RuntimeError, match="IPv4 firewall contains unexpected rules"):
        validate_firewall_rules(rules, ipv6=False)


def test_probe_preserves_ipv6_established_only_policy():
    rules = _ipv6_rules()
    rules.append("-A OUTPUT -d 2001:db8::1/128 -j ACCEPT")
    with pytest.raises(RuntimeError, match="IPv6 firewall allow rules are unexpected"):
        validate_firewall_rules(rules, ipv6=True)


def test_probe_accepts_iptables_save_tcp_rule_order_and_implicit_module():
    rules = _ipv4_rules()
    rules[-1] = (
        f"-A OUTPUT -d {GATEWAY}/32 -p tcp -m tcp --dport {GATEWAY_PORT} -j ACCEPT"
    )
    validate_firewall_rules(rules, ipv6=False)


@pytest.mark.parametrize(
    "rule",
    [
        f"-A OUTPUT -d 203.0.113.7/32 -p tcp -m tcp --dport {GATEWAY_PORT} -j ACCEPT",
        f"-A OUTPUT -d {GATEWAY}/32 -p tcp -m tcp --dport 8081 -j ACCEPT",
        f"-A OUTPUT -d {GATEWAY}/32 -p tcp -p tcp -m tcp --dport {GATEWAY_PORT} -j ACCEPT",
        f"-A OUTPUT -d {GATEWAY}/32 -p tcp -m udp --dport {GATEWAY_PORT} -j ACCEPT",
    ],
)
def test_probe_rejects_changed_target_port_duplicate_or_changed_module(rule):
    rules = _ipv4_rules()
    rules[-1] = rule
    with pytest.raises(RuntimeError, match="IPv4 firewall allow rules are unexpected"):
        validate_firewall_rules(rules, ipv6=False)
