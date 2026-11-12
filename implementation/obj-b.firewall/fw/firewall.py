"""A stateless firewall: an ordered access list checked first match wins.

``acl.tsv`` has one ``address<TAB>mask<TAB>action`` rule per line, where action is
``allow`` or ``deny``. A packet matches when ``dst_ip & mask == address & mask``,
so a mask can be a prefix or a wildcard, like ``255.0.0.240`` for hosts .240 to .255
in every ``10.x.y.0/24``. A packet that matches no rule is allowed.

Checking costs time in proportion to the rules checked, as a linear rule chain does.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ALLOW = "allow"
DENY = "deny"
# Hosts .240 to .255 of every router's /24 are the blocked servers.
BLOCKED_ADDRESS = "10.0.0.240"
BLOCKED_MASK = "255.0.0.240"
FIRST_BLOCKED_HOST = 240


def parse_address(text: str) -> int:
    octets = text.split(".")
    if len(octets) != 4 or not all(o.isdigit() and 0 <= int(o) <= 255 for o in octets):
        raise ValueError(f"bad IPv4 address {text!r}")
    value = 0
    for octet in octets:
        value = (value << 8) | int(octet)
    return value


@dataclass(frozen=True)
class Rule:
    address: int
    mask: int
    action: str

    def matches(self, dst_ip: int) -> bool:
        return dst_ip & self.mask == self.address & self.mask


class Firewall:
    def __init__(self, rules: list[Rule]) -> None:
        self.rules = rules

    @classmethod
    def load(cls, path: str | Path) -> "Firewall":
        rules = []
        for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip() or line.startswith("#"):
                continue
            fields = line.split("\t")
            if len(fields) != 3 or fields[2] not in (ALLOW, DENY):
                raise ValueError(f"{path}:{number}: expected address<TAB>mask<TAB>allow|deny")
            rules.append(Rule(parse_address(fields[0]), parse_address(fields[1]), fields[2]))
        return cls(rules)

    def check(self, dst_ip: int) -> tuple[bool, int]:
        """Return ``(allowed, rules checked)``."""
        for checked, rule in enumerate(self.rules, 1):
            if rule.matches(dst_ip):
                return rule.action == ALLOW, checked
        return True, len(self.rules)


def write_acl(rules: int, path: str | Path) -> None:
    """``rules - 1`` rules no packet matches, then the rule that blocks the servers.

    The filler rules deny single addresses in ``10.255.0.0/16``, which no router in
    a network under 65280 routers owns. Every packet therefore checks every rule,
    so a visit's firewall cost is the same for allowed and denied packets.
    """
    if rules < 1:
        raise ValueError("a firewall needs at least one rule")
    lines = [f"10.255.{i >> 8 & 255}.{i & 255}\t255.255.255.255\t{DENY}" for i in range(rules - 1)]
    lines.append(f"{BLOCKED_ADDRESS}\t{BLOCKED_MASK}\t{DENY}")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
