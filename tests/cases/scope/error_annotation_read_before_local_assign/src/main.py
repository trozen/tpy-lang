# A local annotation of a module global's name makes the name the function's
# local: the read before it is CPython's UnboundLocalError, never the
# module global.
from tpy import int32

G = 7


def main() -> None:
    # The read the later annotated binding makes local.
    print(G)  # tpyc: error(/'G' is read before it is bound on line 12: .* does not see the module's 'G'/)
    G: int32 = 1
    print(G)


main()
