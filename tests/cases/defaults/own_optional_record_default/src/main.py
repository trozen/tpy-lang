# Own[Rec | None] default on a free function: Own forces the value form, so the
# pointer-form escape must not leave an illegal C++ default at the declaration.

from tpy import int64, Own


class Rec:
    def __init__(self, v: int64) -> None:
        self.v = v


class Holder:
    # A ctor is a member, so this Own default keeps its C++ spelling.
    def __init__(self, r: Own[Rec | None] = None) -> None:
        self.slot = r

    def value(self) -> int64:
        if self.slot is None:
            return -1
        return self.slot.v


# The subject: on a FREE function the same Own default has no C++ spelling --
# Own renders the value form, so the pointer-form escape must not apply and the
# declaration must not carry `= std::nullopt` ahead of Rec's definition.
def store(r: Own[Rec | None] = None) -> Own[Holder]:
    return Holder(r)


def main() -> None:
    print(Holder().value())
    print(Holder(Rec(7)).value())

    print(store().value())  # omitted -> filled at this call site

    kept = store(Rec(4))
    print(kept.value())
    if kept.slot is not None:
        kept.slot.v = 9  # mutation lands on the moved-in value, not a copy
    print(kept.value())


main()
