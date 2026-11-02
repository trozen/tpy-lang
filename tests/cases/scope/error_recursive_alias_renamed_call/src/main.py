# A call of a recursive union alias imported under another name: the alias
# names no constructor here (CPython raises TypeError calling a type alias),
# whatever the local spelling binds.
from defs import Tree as T
from tpy import int32

Tree = 9


def main() -> None:
    # The call of the renamed alias.
    t = T(5)  # tpyc: error(/Type alias 'T' is not callable/)
    print("T", t, Tree)


main()
