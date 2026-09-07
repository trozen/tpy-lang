# A SET comprehension at a dict-value element slot: that element family has no
# witnessed render, so the literal rejects.
from tpy import Int32


def main() -> None:
    xs: list[Int32] = [1, 2]
    d = {"s": {x * 2 for x in xs}}  # tpyc: error(/expr.container_literal/)
    print(d)


main()
