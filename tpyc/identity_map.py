"""Identity-keyed containers that own their keys.

A fact keyed by an object must keep that object alive for as long as the fact
is readable. A plain ``{id(node): fact}`` table does not: CPython recycles
addresses, so once the keyed node dies a later node allocated at the same
address inherits its entry -- silently, and only under some heap layouts.
``IdentityMap`` / ``IdentitySet`` store the key alongside the value, which
makes the address unrecyclable while the entry lives.

A ``WeakKeyDictionary`` is not an option here: the AST nodes these tables key
on are plain ``@dataclass``es, so they generate ``__eq__`` and are unhashable.
Identity is therefore still expressed as ``id(key)`` internally -- but never as
the only reference to the key.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Generic, Iterable, Iterator, TypeVar

K = TypeVar("K")
V = TypeVar("V")
T = TypeVar("T")


class IdentityMap(Generic[K, V]):
    """A dict keyed by object identity that holds a reference to each key.

    Mirrors the ``dict`` surface the converted side tables used, so call sites
    change only by dropping the ``id(...)`` wrapper.
    """

    __slots__ = ("_d",)

    def __init__(self, source: "IdentityMap[K, V] | Iterable[tuple[K, V]] | None" = None) -> None:
        self._d: dict[int, tuple[K, V]] = {}
        if source is not None:
            self.update(source)

    def __getitem__(self, key: K) -> V:
        return self._d[id(key)][1]

    def __setitem__(self, key: K, value: V) -> None:
        self._d[id(key)] = (key, value)

    def __delitem__(self, key: K) -> None:
        del self._d[id(key)]

    def __contains__(self, key: object) -> bool:
        return id(key) in self._d

    def __len__(self) -> int:
        return len(self._d)

    def __iter__(self) -> Iterator[K]:
        return (k for k, _ in self._d.values())

    def __bool__(self) -> bool:
        return bool(self._d)

    def get(self, key: K, default: Any = None) -> "V | Any":
        entry = self._d.get(id(key))
        return default if entry is None else entry[1]

    def setdefault(self, key: K, default: V) -> V:
        entry = self._d.get(id(key))
        if entry is None:
            self._d[id(key)] = (key, default)
            return default
        return entry[1]

    def pop(self, key: K, *default: Any) -> "V | Any":
        entry = self._d.pop(id(key), None)
        if entry is None:
            if default:
                return default[0]
            raise KeyError(key)
        return entry[1]

    def keys(self) -> Iterator[K]:
        return iter(self)

    def values(self) -> Iterator[V]:
        return (v for _, v in self._d.values())

    def items(self) -> Iterator[tuple[K, V]]:
        return iter(self._d.values())

    def clear(self) -> None:
        self._d.clear()

    def update(self, source: "IdentityMap[K, V] | Iterable[tuple[K, V]]") -> None:
        if isinstance(source, IdentityMap):
            self._d.update(source._d)
            return
        for key, value in source:
            self[key] = value

    def copy(self) -> "IdentityMap[K, V]":
        new: IdentityMap[K, V] = IdentityMap()
        new._d = dict(self._d)
        return new

    def __deepcopy__(self, memo: dict) -> "IdentityMap[K, V]":
        # Keys are carried over BY IDENTITY, never cloned: a snapshot of a
        # table whose keys were copies would be looked up with the live AST
        # nodes and miss every entry. (`SemanticContext.save_function_state`
        # deep-copies the whole per-function state, and the copy is what the
        # restore installs.) Values still copy deeply, so a trial's mutations
        # to them stay isolated.
        new: IdentityMap[K, V] = IdentityMap()
        memo[id(self)] = new
        new._d = {k: (key, deepcopy(value, memo))
                  for k, (key, value) in self._d.items()}
        return new

    def __repr__(self) -> str:
        return f"IdentityMap({list(self._d.values())!r})"


class IdentitySet(Generic[T]):
    """A set of objects compared by identity that holds a reference to each
    member -- the ``set``-shaped sibling of ``IdentityMap``."""

    __slots__ = ("_d",)

    def __init__(self, source: "Iterable[T] | None" = None) -> None:
        self._d: dict[int, T] = {}
        if source is not None:
            self.update(source)

    def add(self, item: T) -> None:
        self._d[id(item)] = item

    def discard(self, item: T) -> None:
        self._d.pop(id(item), None)

    def remove(self, item: T) -> None:
        del self._d[id(item)]

    def __contains__(self, item: object) -> bool:
        return id(item) in self._d

    def __len__(self) -> int:
        return len(self._d)

    def __iter__(self) -> Iterator[T]:
        return iter(self._d.values())

    def __bool__(self) -> bool:
        return bool(self._d)

    def update(self, source: "Iterable[T]") -> None:
        if isinstance(source, IdentitySet):
            self._d.update(source._d)
            return
        for item in source:
            self.add(item)

    def __ior__(self, source: "Iterable[T]") -> "IdentitySet[T]":
        self.update(source)
        return self

    def clear(self) -> None:
        self._d.clear()

    def copy(self) -> "IdentitySet[T]":
        new: IdentitySet[T] = IdentitySet()
        new._d = dict(self._d)
        return new

    def __deepcopy__(self, memo: dict) -> "IdentitySet[T]":
        # Members ARE the keys, so a deep copy keeps every one of them by
        # identity -- see `IdentityMap.__deepcopy__`.
        new: IdentitySet[T] = IdentitySet()
        memo[id(self)] = new
        new._d = dict(self._d)
        return new

    def __repr__(self) -> str:
        return f"IdentitySet({list(self._d.values())!r})"
