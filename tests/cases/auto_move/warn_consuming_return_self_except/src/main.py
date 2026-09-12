# The except-handler sibling of consuming_return_self_finally. A consuming
# method's `self` is an owned source, but a `return self` the HANDLER can still
# read past is not its last use, so it cannot move: it copies and says so,
# exactly as any other non-last-use owned source at an owning slot does. Only
# the handler's own `return self` is the last use and moves silently. The copy
# is the acknowledged divergence, so main prints only what both sides agree on.
from typing import Self
from tpy import int32, Own


class Widget:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def consume(self: Own[Self]) -> Own[Self]:
        try:
            if self.n > 0:
                raise ValueError("bad")
            # The handler below reads `self` again, so this is not a last
            # use and the owning return copies.
            return self  # tpyc: warning(/copies Widget into owned storage/)
        except ValueError:
            print("handler sees", self.n)
            # The last use on this path: moves, no diagnostic.
            return self  # tpyc: ok


def main() -> None:
    print(Widget(5).consume().n)


main()
