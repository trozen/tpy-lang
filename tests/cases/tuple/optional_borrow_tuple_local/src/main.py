# A nullable borrow-form tuple local (`tuple[..., ref] | None`) lowers to
# std::optional<std::tuple<..., T*>>: the inner tuple takes borrow form so its
# reference elements ALIAS the storage source on rebind (matching CPython)
# instead of copying. Covers: storage-source init + rebind (a), per-element-Own
# call init then alias rebind (b), None init + conditional rebind (c), and a
# local FIRST-DECLARED inside a branch (d, the hoisted-decl path). Each case
# mutates through the narrowed alias and observes the change on the shared
# source to force the value-vs-reference distinction.
from tpy import Own


class Box:
    val: int

    def __init__(self, v: int):
        self.val = v


class Holder:
    pair: tuple[int, Box]

    def __init__(self, b: Box):
        self.pair = (1, b)  # tpyc: warning(/copies Box into field/)


def make_pair(v: int) -> tuple[int, Own[Box]]:
    return (v, Box(v))


def alias_storage() -> int:
    h = Holder(Box(5))
    h2 = Holder(Box(7))
    t: tuple[int, Box] | None = h.pair
    t = h2.pair
    if t is not None:
        t[1].val = 99  # tpyc: ok
    return h2.pair[1].val  # 99 -- aliased, not the original 7


def alias_after_owning_call(h: Holder) -> None:
    t: tuple[int, Own[Box]] | None = make_pair(9)
    # The rebind aliases h.pair (mutation below is observed on the source in
    # main), but sema spuriously warns it copies -- a known-wrong diagnostic
    # tracked in BUGS.md (the remediation it suggests would defeat the alias).
    t = h.pair  # tpyc: warning(/copies tuple\[int, Box\] into owned storage/)
    if t is not None:
        t[1].val = 77


def conditional(h: Holder, flag: bool) -> int:
    t: tuple[int, Box] | None = None
    if flag:
        t = h.pair
    if t is not None:
        t[1].val = 42
        return t[1].val
    return -1


def branch_declared(h: Holder, flag: bool) -> int:
    # First DECLARED inside the `if` branch (hoisted for the post-branch read),
    # so the decl flows through the branch-decl path, not the straight-line one.
    if flag:
        t: tuple[int, Box] | None = h.pair
    else:
        t = None
    if t is not None:
        t[1].val = 55
    return h.pair[1].val


def main() -> None:
    print(alias_storage())

    h3 = Holder(Box(3))
    alias_after_owning_call(h3)
    print(h3.pair[1].val)  # 77 -- aliased after rebind to h3.pair

    h4 = Holder(Box(1))
    print(conditional(h4, True))   # 42
    print(h4.pair[1].val)          # 42 -- mutation visible on source
    print(conditional(h4, False))  # -1 -- stayed nullopt

    h5 = Holder(Box(2))
    print(branch_declared(h5, True))   # 55 -- aliased through branch-declared local
    print(branch_declared(h5, False))  # 55 -- stayed nullopt, source unchanged


main()
