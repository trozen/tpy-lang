# In-place `+=` on a str PARAM: the param is a borrowed view, so appending to
# it would need an owned copy in the prologue -- outside the in-place append
# arm, so `s += "!"` is rejected. The owned-local target is pinned by
# tests/cases/str/str_view_init_coerce.


def shout(s: str) -> str:
    s += "!"  # tpyc: error(/stmt\.aug_assign/)
    return s


def main() -> None:
    print(shout("hi"))


main()
