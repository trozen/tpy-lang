# Module-level helpers the case reaches through a module-qualified call.
from tpy import int32, Span


def mk_span(xs: Span[int32]) -> Span[int32]:
    return xs


class Holder:
    xs: list[int32]

    def __init__(self) -> None:
        self.xs = [7, 8]


def get_xs(h: Holder) -> list[int32]:
    return h.xs
