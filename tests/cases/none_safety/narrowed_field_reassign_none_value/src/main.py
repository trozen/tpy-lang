# Regression: same shape as narrowed_field_reassign_none_box but for a
# value-Optional field (`int | None`). The field's C++ storage is
# `std::optional<BigInt>`; reassigning to None after narrowing must
# emit `std::nullopt`, not `nullptr`.
class Holder:
    slot: int | None

    def __init__(self) -> None:
        self.slot = 1

    def step(self) -> bool:
        if self.slot is None:
            return False
        self.slot = None
        return True


def main() -> None:
    h = Holder()
    print(h.step())
    print(h.slot is None)


main()
