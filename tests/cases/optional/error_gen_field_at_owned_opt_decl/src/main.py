# The one POSITION at which the owned-inner `Optional` decl of
# `view_at_owned_opt_decl` still rejects: inside a generator. The resumable
# frame reserves no owned-view optional local, so `res.local_storage` stops
# the decl before the source shape is even looked at -- a view PARAM source
# rejects here exactly as the `str` FIELD source below does, so this is a
# frame-layout row, not a gap in the field arm.
from tpy import int32
from typing import Iterator


class Rec:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name


# the frame layout is decided per FUNCTION, so the reject lands on the `def`
def sizes(r: Rec) -> Iterator[int32]:  # tpyc: error(/not yet supported/)
    t: str | None = r.name
    if t is None:
        yield -1
    else:
        yield len(t)


def main() -> None:
    for v in sizes(Rec("hello")):
        print(v)


main()
