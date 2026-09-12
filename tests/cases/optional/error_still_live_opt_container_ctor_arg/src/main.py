# A STILL-LIVE optional-container local at an `Own[list | None]` ctor slot: the
# inline row is last-use gated, and a live source would need a hoisted temp.
# Concretely, `Holder(tag)` passes `tag`, which is read again afterward;
# TPy rejects that call today.
from tpy import int32, Own


class Holder:
    tags: list[str] | None

    def __init__(self, tags: Own[list[str] | None]) -> None:
        self.tags = tags


def build_live(flag: bool) -> int32:
    acc: list[str] = ["a"]
    tag: list[str] | None = None
    if flag:
        tag = list(acc)
    h = Holder(tag)  # tpyc: error(/call.ctor_arg.own_optional/)
    if tag is not None:
        return len(tag)
    return 0


def main() -> None:
    print(build_live(True))


main()
