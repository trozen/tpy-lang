# A record subscript whose __getitem__ variants tie for the key is the explicit
# call's "Ambiguous overload" error, not a silent pick (docs/OVERLOAD_DESIGN.md
# "Subscripts"); a tie among the receiver's preferred variants stays one even
# beside an exact variant of the other kind ("The receiver kind ranks first").
from tpy import dispatch, int8, int64, readonly


class B:
    def __init__(self) -> None:
        pass

    @dispatch
    @readonly(False)
    def __getitem__(self, x: int64 | None) -> str:
        return "optional"

    @dispatch
    @readonly(False)
    def __getitem__(self, x: int64) -> str:
        return "plain"

    @dispatch
    @readonly
    def __getitem__(self, x: int8) -> str:
        return "exact"


def main() -> None:
    b = B()
    n8: int8 = 3
    # the int8 key converts to both variants equally
    print(b[n8])  # tpyc: error(/Ambiguous overload for '__getitem__'/)


main()
