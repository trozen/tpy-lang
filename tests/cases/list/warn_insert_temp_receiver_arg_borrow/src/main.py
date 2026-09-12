# The insert-slot half of warn_return_temp_receiver_arg_borrow: at an element
# slot the same argument-borrow through a temporary receiver is a WARNING, not
# an error. The copy still diverges from CPython (which would alias), so the
# inserted element is deliberately not observed after mutating the source --
# the warning is the subject. Only the record payload is reachable here: the
# container twin (`ys.append(Picker().pick_items(h))`) rejects one layer down
# at `method.arg_shape`, so its witness is the return case.
from tpy import int32


class Row:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Picker:
    def __init__(self) -> None:
        pass

    def pick(self, rows: list[Row]) -> Row:
        return rows[0]


def main() -> None:
    rows = [Row(1)]
    xs: list[Row] = []
    # The receiver is a temporary, but `pick` borrows its ARGUMENT.
    xs.append(Picker().pick(rows))  # tpyc: warning(/copies Row into owned storage/)
    print(xs[0].v)


main()
