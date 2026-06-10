# A nullable borrow-form tuple local (`tuple[..., ref] | None`) lowers to
# std::optional<std::tuple<..., T*>>: the inner tuple takes borrow form so its
# reference elements ALIAS the storage source on rebind (matching CPython)
# instead of copying. Covers: storage-source init + rebind (a), owning-call
# init then alias rebind (b), None init + conditional rebind (c), a local
# FIRST-DECLARED inside a branch (d, the hoisted-decl path), and a SECOND
# owning-call reassignment (e, the negative guard: re-owning must not spuriously
# warn a copy). The alias cases (a-d) mutate through the narrowed alias and
# observe the change on the shared source to force the value-vs-reference
# distinction.
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
    t: tuple[int, Box] | None = make_pair(9)
    # The rebind aliases h.pair (mutation below is observed on the source in
    # main); the local collapses to borrow form, so coercing the borrow source
    # against it must NOT warn about copying into owned storage.
    t = h.pair  # tpyc: ok
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


def reowned(v: int) -> int:
    # Reassigning from a SECOND owning call: the owning rvalue materializes into
    # a slot the local aliases, so this is NOT a copy-into-owned -- it must not
    # warn (the negative guard for the collapse-the-reassignment-target fix).
    t: tuple[int, Box] | None = make_pair(9)
    t = make_pair(v)  # tpyc: ok
    if t is not None:
        t[1].val = 50
        return t[0] + t[1].val
    return -1


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

    print(reowned(5))  # 5 + 50 = 55 -- re-owned, no spurious copy warning


main()
