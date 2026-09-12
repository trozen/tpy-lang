# The argument-borrowing sibling of warn_return_temp_receiver_borrow: a method
# on a TEMPORARY receiver that borrows from an ARGUMENT hands back the caller's
# storage. It takes the same one rule -- a borrow-returning call at an owning
# slot is a borrowed source -- so the Own return slot copies and warns here
# too. The copy is the ACKNOWLEDGED CPython divergence (CPython hands back the
# very Row), so the case prints only what both agree on and the WARNING is the
# pin.
from tpy import int32, Own, copy


class Row:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Picker:
    def __init__(self) -> None:
        pass

    def pick(self, rows: list[Row]) -> Row:
        # Borrows from ARGUMENT 0, not from self.
        return rows[0]


def take(rows: list[Row]) -> Own[Row]:
    # The receiver is a temporary, but the borrow reaches past it into `rows`.
    return Picker().pick(rows)  # tpyc: warning(/copies Row into owned storage/)


def take_copy(rows: list[Row]) -> Own[Row]:
    return copy(Picker().pick(rows))  # tpyc: ok


def main() -> None:
    rows = [Row(1)]
    print(take(rows).v, take_copy(rows).v, rows[0].v)


main()
