# `dict.get(k)` spells its result `auto_readonly[V] | None`: off a readonly
# dict it is readonly, so a write through it is refused.
from tpy import int32, readonly


class P:
    def __init__(self, v: int32) -> None:
        self.v = v


def f(d: readonly[dict[str, P]]) -> None:
    x = d.get("a")
    if x is not None:
        x.v = 5  # tpyc: error(/Cannot mutate readonly reference/)


def main() -> None:
    f({"a": P(1)})


main()
