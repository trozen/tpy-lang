# Container method calls on a dict[Int32, Int32] param, routed through THIR
# (--thir-codegen, byte-identical to the AST path): statement-position
# `d.pop(k)` (a readonly[K] param slot, unwrapped to the scalar key) and
# `d.clear()` (plain @native member), plus value-position `d.pop(k)` /
# `d.pop(k, default)` / `d.get(k, default)` reads. The caller observes the
# removals through the shared dict (reference semantics forced).
from tpy import Int32


def drop(d: dict[Int32, Int32], k: Int32) -> None:
    d.pop(k)


def take(d: dict[Int32, Int32], k: Int32) -> Int32:
    v = d.pop(k)
    return v + d.pop(k, 0)


def peek(d: dict[Int32, Int32], k: Int32) -> Int32:
    return d.get(k, 0)


def wipe(d: dict[Int32, Int32]) -> None:
    d.clear()


def main() -> None:
    scores = {1: 100, 2: 200, 3: 300, 4: 400}
    drop(scores, 1)
    print(len(scores))
    print(take(scores, 2))
    print(peek(scores, 3))
    print(peek(scores, 9))
    wipe(scores)
    print(len(scores))


main()
