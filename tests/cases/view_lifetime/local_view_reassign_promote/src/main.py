# A view local that aliases one source and is later rebound to another must keep
# BOTH sources for the owned-vs-view resolution: here `v` first aliases `a`, then
# is rebound to `b`. `a` is then mutated (its std::string buffer reallocates), so
# `v` -- which was read while it aliased `a` -- must resolve to an OWNED copy.
# If the reassignment dropped `a` from the source set, `v` would stay a
# string_view and the first print would read freed storage (cpy phase catches the
# garbage). The read of `v` after the mutation proves the value survived.
def make() -> str:
    return "padded long string that dodges the small-string buffer optimization"


def main() -> None:
    a = make()
    b = make()
    v = a
    a += " appended text that forces a's buffer to reallocate somewhere new"
    print(len(v))   # v aliased a (now reallocated) -> v must be owned
    v = b
    print(len(v))


main()
