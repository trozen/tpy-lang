"""Shared pending-declaration and conditional-initialization scheduling."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import re
from typing import Generic, TypeVar

T = TypeVar("T")


def banks_in_region(cpp_type: str, movable: bool | None) -> bool:
    # Optional backing cannot spell auto or references; both consumers must
    # agree so conditional construction never becomes an eager side effect.
    stripped = cpp_type.strip()
    return bool(movable) and not stripped.endswith("&") and not re.match(r"^(const\s+)?auto\b", stripped)


@dataclass(eq=False)
class TempEntry(Generic[T]):
    value: T
    optional: bool
    deferred: bool = False


class TempQueue(Generic[T]):
    """Closing a lazy region only defers entries not already flushed inside it."""

    def __init__(self) -> None:
        self.pending: list[TempEntry[T]] = []
        self.regions: list[list[TempEntry[T]]] = []

    def begin(self) -> list[TempEntry[T]]:
        region: list[TempEntry[T]] = []
        self.regions.append(region)
        return region

    def end(self, region: list[TempEntry[T]]) -> tuple[TempEntry[T], ...]:
        current = self.regions.pop()
        assert current is region
        live = set(self.pending)
        deferred = tuple(entry for entry in region if entry in live)
        for entry in deferred:
            entry.deferred = True
        return deferred

    @contextmanager
    def eager(self) -> Iterator[None]:
        """No region is open inside: an entry registered there is declared
        before its statement, whatever region encloses it. A region opened
        inside still banks what it holds."""
        outer, self.regions = self.regions, []
        try:
            yield
        finally:
            self.regions = outer

    def register(self, value: T, bankable: bool) -> TempEntry[T]:
        entry = TempEntry(value, bool(self.regions) and bankable)
        if entry.optional:
            self.regions[-1].append(entry)
        self.pending.append(entry)
        return entry

    def drain(self, start: int = 0) -> tuple[TempEntry[T], ...]:
        entries = tuple(self.pending[start:])
        del self.pending[start:]
        return entries
