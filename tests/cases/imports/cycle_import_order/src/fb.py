# Under `from __future__ import annotations` no annotation is evaluated at
# import, so naming the suspended `a` in one is fine.
from __future__ import annotations
import a


def show(it: a.Item) -> None:  # tpyc: ok
    it.n += 100
    print("fb:", it.n)


# A class-field annotation is deferred too.
class Tag:
    k: a.Kind  # tpyc: ok

    def __init__(self, k: a.Kind) -> None:
        self.k = k


def tag_value() -> int:
    return Tag(a.Kind.X).k.value
