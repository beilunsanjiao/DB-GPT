"""Policies for read-only SQL preparation."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SqlGuardPolicy:
    """Conservative capabilities enabled for governed ChatDB queries."""

    reject_comments: bool = True
    reject_projection_star: bool = True
    max_joins: int = 4
