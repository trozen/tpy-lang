# Shapes that keep their refusals: a narrowed non-aggregate payload bound alone, and a
# write of the narrowed field itself, on a `str | None` field MIR models.
from tpy import int32


class SH:
    s: str | None

    def __init__(self) -> None:
        self.s = "a"


# BUGS.md#narrowed-optional-str-field-local-copies: the binding copies the str
def str_payload(h: SH) -> int32:  # tpyc: mir(uncovered /borrow of an Optional field's payload/)
    if h.s is not None:
        s = h.s
        return len(s)
    return 0


def str_append(h: SH) -> None:  # tpyc: mir(uncovered /optional field payload write/)
    if h.s is not None:
        h.s += "b"


def main() -> None:
    h = SH()
    str_append(h)
    print("refused", str_payload(h))
