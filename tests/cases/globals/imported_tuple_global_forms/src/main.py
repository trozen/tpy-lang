# An IMPORTING module sees a tuple global by its binding type alone, so which
# form the global takes (a tuple of pointer slots for a tuple of references,
# storage for one that owns a fresh element) is recorded on the binding by the
# defining module and read here: a write through the imported alias reaches
# the defining module's object, and the owned element reads bare.
from helper import pair, owned, V


def main() -> None:
    pair[0].v = 9  # tpyc: ok
    print("borrow", V.v, pair[1])
    owned[1].v = 7  # tpyc: ok
    print("owned", owned[1].v)


main()
