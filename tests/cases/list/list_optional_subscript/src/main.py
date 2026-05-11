# Element access on `list[P | None]`: subscript-then-access and var-decl
# from subscript. The container holds `optional<P>` (storage form); consumer
# sites lift via `optional_to_ptr` so the access works as if the element
# were pointer-form `P | None`.
from tpy import Int32


class P:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x


def main() -> None:
    pairs: list[P | None] = [P(Int32(1)), None, P(Int32(3))]
    # Subscript-then-access (narrowing path -- runtime null check inserted)
    if pairs[0] is not None:
        print(pairs[0].x)
    # Var-decl from subscript: P* alias of optional<P> slot
    first = pairs[0]
    if first is not None:
        print(first.x)
    second = pairs[1]
    if second is None:
        print(-1)


main()
