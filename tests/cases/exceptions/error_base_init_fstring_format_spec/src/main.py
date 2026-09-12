# A `super().__init__` message built from an f-string with a FORMAT SPEC:
# the base-init cell admits a bare interpolation only, since a spec spells a
# render of its own.
from tpy import int32


class Tagged(Exception):
    n: int32

    def __init__(self, n: int32) -> None:  # tpyc: error(/ctor\.base_init/)
        # The format spec is what puts this message outside the cell.
        super().__init__(f"tag{n:04d}")
        self.n = n


def main() -> None:
    try:
        raise Tagged(5)
    except Tagged as e:
        print(e.n)


main()
