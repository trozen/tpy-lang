from typing import Protocol, runtime_checkable


@runtime_checkable
class Root(Protocol):
    def f(self) -> str: ...


# Below Root so CPython, importing a first, has Root defined when b's
# top-level import of it runs.
from b import Mid, Lone  # noqa: E402  # tpyc: warning(/Import statement should be at the top/)


class Child(Mid, Protocol):
    pass


# Its only ancestor is the undecorated Lone in the cycle peer, so the
# walk crosses the module boundary and still finds nothing checkable.
class LoneChild(Lone, Protocol):
    pass
