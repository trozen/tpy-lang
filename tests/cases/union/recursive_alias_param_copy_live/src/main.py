# A reference-typed member still live after the call is copied into a
# recursive-alias parameter (`t: Tree`): the callee writes the copy, where
# CPython writes the caller's object -- warned at the argument.
from fixtures import A, C, M, Tree, bump, gbump


def free_function() -> None:
    c = A(1)
    # The callee's write misses `c`.
    print("free live", bump(c), c.n)  # tpyc: warning(/copies A into Tree parameter 't'; use copy\(\) to make this explicit/)
    xs: list[Tree] = [1]
    # A list member copies its elements too; the callee's append misses `xs`.
    print("free list", bump(xs), len(xs))  # tpyc: warning(/copies list\[Tree\] into Tree parameter 't'/)


def method() -> None:
    m = M()
    c = A(1)
    print("method live", m.bump(c), c.n)  # tpyc: warning(/copies A into Tree parameter 't' of M.bump/)


def generic_alias() -> None:
    g = C[A]()
    g.items.append(A(1))
    print("generic live", gbump(g), g.items[0].n)  # tpyc: warning(/copies C\[A\] into GTree\[A\] parameter 't'/)


def main() -> None:
    free_function()
    method()
    generic_alias()


main()
