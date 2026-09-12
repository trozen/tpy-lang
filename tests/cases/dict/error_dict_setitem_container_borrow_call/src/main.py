# The adjacent shape to the container-element call store: a BORROW-returning
# call aliases its receiver, so filling the by-value element slot from it is a
# copy the bare forward does not spell -- it keeps rejecting.
from tpy import int32


class Source:
    rows: list[int32]

    def __init__(self) -> None:
        self.rows = [1, 2]

    def borrow(self) -> list[int32]:
        return self.rows


def main() -> None:
    s = Source()
    d: dict[str, list[int32]] = {}
    d["k"] = s.borrow()  # tpyc: error(/setitem\.container_value_shape/)
    print(len(d))


main()
