# An IMPORTING module sees a tuple global by its binding type alone: a tuple
# of pointer slots, whether the defining module bound it from names or parked
# a fresh element in a static, so a write through the imported global reaches
# the defining module's object and every reference element reads through its
# slot (`std::get<1>(::tpyapp::helper::owned)->v`).
from helper import pair, owned, V


def main() -> None:
    pair[0].v = 9  # tpyc: ok
    print("borrow", V.v, pair[1])
    owned[1].v = 7  # tpyc: ok
    print("owned", owned[1].v)


main()
