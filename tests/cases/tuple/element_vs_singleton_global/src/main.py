# PINS A KNOWN-WRONG SHIPPING STATE (BUGS.md#global-tuple-ref-storage-form).
#
# A tuple element at a GLOBAL does NOT behave as the same type would as a
# singleton there. The scalar global takes a borrow slot (`Box*`) and ALIASES,
# matching CPython; the tuple global takes owning storage and COPIES, silently
# -- no warning at either shape, unlike every other owning sink in this family.
#
# The mixed form is worse than inherited: before the storage-form wave it failed
# the C++ build outright, so the wave turned a loud error into a silently wrong
# program. That is why this case exists -- the cell had no coverage at all,
# which is how it survived the census that found it.
#
# The two tuple globals carry NO annotation on purpose: `# tpyc: ok` would
# assert the silence is intended, when it is the defect. diag.txt pins it.
#
# CPython prints 42 43 44. When the global slot learns the borrow form the
# local already uses, the scalar and the all-borrow tuple reach 42 43; the
# mixed form reaches 44 only if it stops taking storage, which is an open
# question -- so expect 42 43 2 or 42 43 44 depending on how that lands.
from tpy import Int32, Own


class Box:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


def make_mixed(b: Box) -> tuple[Own[Box], Box]:
    return (Box(1), b)


V = Box(2)
singleton: Box = V  # tpyc: warning(/will not keep the object/)
pair: tuple[Box, Box] = (V, V)
mixed: tuple[Box, Box] = make_mixed(V)


def main() -> None:
    # Correct: the scalar global is a borrow slot, so this reaches V.
    singleton.n = 42
    print(V.n)

    V.n = 2
    # WRONG (silent): the tuple global copied, so this write is lost.
    pair[0].n = 43
    print(V.n)

    V.n = 2
    # WRONG (silent), and only reachable at all since the storage-form wave.
    mixed[1].n = 44
    print(V.n)


main()
