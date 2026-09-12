# Iterating `self.pairs: list[P | None]` from a @readonly method: the
# loop var binds `const optional<P>&` (const-source iteration), and a
# var-decl from the loop var must propagate const to the receiving
# pointer-local so `optional_to_ptr` returns `const P*`.
from tpy import int32, readonly


class P:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x


class Holder:
    pairs: list[P | None]
    def __init__(self) -> None:
        self.pairs = [P(int32(1)), None, P(int32(3))]

    @readonly
    def first_nonnull(self) -> int32:
        for it in self.pairs:
            # `it` is in const_storage_form_optional_locals; the var-decl
            # below must declare `const P* first = optional_to_ptr(it)`.
            first = it
            if first is not None:
                return first.x
        return int32(-1)


def main() -> None:
    h = Holder()
    print(h.first_nonnull())


main()
