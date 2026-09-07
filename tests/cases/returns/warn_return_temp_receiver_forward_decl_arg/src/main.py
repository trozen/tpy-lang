# The argument-borrowing sibling of warn_return_temp_receiver_forward_decl:
# the same forward-declared shape, with the callee borrowing from an ARGUMENT.
# Declaration order changes nothing -- the line and the text match
# warn_return_temp_receiver_arg_borrow, which is this program with `Picker`
# declared FIRST. The copy is the ACKNOWLEDGED CPython divergence, so the case
# prints only what both agree on.
from tpy import Int32, Own, copy


class Row:
    v: Int32

    def __init__(self, v: Int32) -> None:
        self.v = v


class Holder:
    def take(self, rows: list[Row]) -> Own[Row]:
        # Receiver is a temporary, but `Picker.pick` is not analyzed yet.
        return Picker().pick(rows)  # tpyc: warning(/copies Row into owned storage/)

    def take_copy(self, rows: list[Row]) -> Own[Row]:
        return copy(Picker().pick(rows))  # tpyc: ok


class Picker:
    def __init__(self) -> None:
        pass

    def pick(self, rows: list[Row]) -> Row:
        # Borrows from ARGUMENT 0, not from self.
        return rows[0]


def main() -> None:
    rows = [Row(1)]
    print(Holder().take(rows).v, Holder().take_copy(rows).v, rows[0].v)


main()
