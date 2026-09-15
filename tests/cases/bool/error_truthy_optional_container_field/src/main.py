# The adjacent shape that must keep rejecting: an Optional[CONTAINER] field in
# truthy position. The bare std::optional engagement test would answer "is not
# None" where Python also asks "is it non-empty", so the read stays a located
# reject instead of a silent divergence.
from tpy import int32


class Bag:
    items: list[int32] | None

    def __init__(self) -> None:
        self.items = None


def main() -> None:
    b = Bag()
    if b.items:  # tpyc: error(/not yet supported by C\+\+ code generation/)
        print("full")


main()
