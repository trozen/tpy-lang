# The method-arg sink's prologue fences an `Own[view]` slot to LITERAL
# sources, because a container's view-key INSERT would need a copy+move view
# temp no row builds. That fence is about the INSERT, so it rejects at a
# builtin-stub slot only: a user record's `Own[StrView]` param is an ordinary
# by-value slot whose NAME source the Own cascade rows answer for.
from tpy import Int32, Own, StrView


class Bag:
    n: Int32

    def __init__(self) -> None:
        self.n = 0

    # The body cannot READ an Own[view] param yet (`name.own_read`), so the
    # case is about the ARGUMENT the call binds, not about `s`.
    def keep(self, s: Own[StrView]) -> None:
        self.n += 1


def main() -> None:
    b = Bag()
    name = "abcd"
    # A str NAME at a user record's Own[StrView] slot.
    b.keep(name)  # tpyc: ok
    print(b.n, name)


main()
