# A comprehension whose element/value is a bare NAME at a CONTAINER slot
# (list / Array / dict value). TPy stores nested containers inline, so each slot
# COPIES the named container -- deliberate, and the warning on every one of the
# three lines is the evidence; CPython would alias, so the case only reads the
# copies (a post-comprehension mutation of the source would diverge by design).
# The source must stay LIVE past the comprehension: at a last-use read sema
# believes the name moves and drops the warning, and the silent copy rejects.
from tpy import Int32, Own


def mk() -> Own[list[Int32]]:
    return [1, 2]


def main() -> None:
    xs = mk()
    xs.append(3)
    # A list slot: the element name lands bare and the slot init copies.
    ls: list[list[Int32]] = [xs for i in range(2)]  # tpyc: warning(/copies list/)
    # ... the dict VALUE slot takes the same bare read.
    dv: dict[Int32, list[Int32]] = {i: xs for i in range(2)}  # tpyc: warning(/copies list/)
    # ... and an Array-demoted comp over a literal-proven range warns too.
    arr = [xs for i in range(3)]  # tpyc: warning(/copies list/)
    print(len(ls), len(ls[0]), len(dv), len(dv[0]), len(arr), len(arr[2]))
    print(ls[1][2], dv[1][0], arr[0][1])
    # Reading xs here is what keeps the three reads above off their last use.
    print(len(xs))


main()
