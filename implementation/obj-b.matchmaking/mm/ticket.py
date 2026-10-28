"""Party-ticket job ids.

The CSV stays a three-column Lab 0 row. Region, mode, skill band, and party
size ride in the job id: ``t{seq}.{region}.{mode}.{band}.p{party}``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

REGIONS = ("na", "eu", "ap", "sa")
MODES = ("ranked", "unrated")
BANDS = ("placement", "low", "mid", "high", "apex")

_TICKET_RE = re.compile(
    rf"^t(\d+)\.({'|'.join(REGIONS)})\.({'|'.join(MODES)})\.({'|'.join(BANDS)})\.p([1-5])$"
)


@dataclass(frozen=True)
class Ticket:
    """One party asking for a match."""

    seq: int
    region: str
    mode: str
    band: str
    party: int

    @property
    def job_id(self) -> str:
        return format_ticket_id(self.seq, self.region, self.mode, self.band, self.party)


def format_ticket_id(seq: int, region: str, mode: str, band: str, party: int) -> str:
    """Build a job id. Raises ``ValueError`` when a token is outside the grammar."""
    if seq < 0:
        raise ValueError("seq must be >= 0")
    if region not in REGIONS:
        raise ValueError(f"unknown region {region}")
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode}")
    if band not in BANDS:
        raise ValueError(f"unknown band {band}")
    if party < 1 or party > 5:
        raise ValueError("party must be 1..5")
    return f"t{seq}.{region}.{mode}.{band}.p{party}"


def parse_ticket(job_id: str) -> Ticket | None:
    """Return the ticket, or None when ``job_id`` is not a party ticket."""
    match = _TICKET_RE.match(job_id)
    if match is None:
        return None
    return Ticket(
        seq=int(match.group(1)),
        region=match.group(2),
        mode=match.group(3),
        band=match.group(4),
        party=int(match.group(5)),
    )
