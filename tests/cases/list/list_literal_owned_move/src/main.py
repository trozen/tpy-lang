# A last-use @nocopy local in a list/array LITERAL moves rather than copies;
# @nocopy makes a silent copy a hard compile error, so a passing build proves it.
from tpy import Int32
from tplib.box import Box


def array_lit() -> None:
    p = Box(1)
    xs = [p]
    print(len(xs), xs[0].get())


def vector_lit() -> None:
    p = Box(2)
    q = Box(3)
    # annotated list[...] forces the std::vector (not fixed-size Array) path
    xs: list[Box[Int32]] = [p, q]
    print(len(xs), xs[0].get(), xs[1].get())


def not_last_use() -> None:
    inner = [1, 2]
    # inner read after -> copied, not moved (asserts the over-trigger guard)
    xs = [inner]  # tpyc: warning(/copies .* into owned storage/)
    inner.append(3)
    print(len(inner), len(xs))  # 3 1 -- inner intact (would be 0 if wrongly moved)


def main() -> None:
    array_lit()
    vector_lit()
    not_last_use()


main()
