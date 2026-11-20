"""Normalized authentication principal model."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Principal:
    subject: str
    roles: list[str] = field(default_factory=list)
    scopes: list[str] = field(default_factory=list)
