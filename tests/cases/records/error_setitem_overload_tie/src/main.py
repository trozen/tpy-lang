# A record store whose __setitem__ variants tie for the key is the explicit
# call's "Ambiguous overload" error, not a silent pick (docs/OVERLOAD_DESIGN.md
# "Subscripts").
from tpy import dispatch, int8, int32, int64


class S:
    log: list[str]

    def __init__(self) -> None:
        self.log = []

    @dispatch
    def __setitem__(self, x: int64 | None, v: int32) -> None:
        self.log.append("set-optional")

    @dispatch
    def __setitem__(self, x: int64, v: int32) -> None:
        self.log.append("set-plain")


def main() -> None:
    s = S()
    n8: int8 = 3
    # the int8 key converts to both variants equally
    s[n8] = 1  # tpyc: error(/Ambiguous overload for '__setitem__'/)
    print(s.log)


main()
