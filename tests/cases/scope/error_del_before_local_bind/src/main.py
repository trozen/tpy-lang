# `del` of a name the function binds only later: the name is the function's
# local, so the `del` is CPython's UnboundLocalError, never a deletion of the
# module global.
from tpy import int32

G = 7


def main() -> None:
    # The `del` the later `G = 1` makes local.
    del G  # tpyc: error(/'G' is deleted before it is bound on line 12: .* so this deletion does not see the module's 'G'/)
    G = 1
    print(G)


main()
