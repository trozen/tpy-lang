# A reference-typed member passed to a recursive-alias parameter (`t: Tree`)
# builds the alias from it: a last use moves in, a fresh value is built in
# place, an explicit copy() is silent, and a value member or an argument
# already of the alias type converts nothing. The warned copy of a live
# argument is in recursive_alias_param_copy_live (its output diverges).
from tpy import copy
from fixtures import A, C, M, Tree, bump, gbump, peek


def free_function() -> None:
    a = A(1)
    # Last use: the alias takes the object over.
    print("free last", bump(a))  # tpyc: ok
    # Fresh: built straight into the alias.
    print("free fresh", bump(A(2)))  # tpyc: ok
    c = A(3)
    # Explicit copy: the callee writes the copy, as in CPython.
    print("free copy", bump(copy(c)), c.n)  # tpyc: ok
    # A value member and an alias-typed argument convert nothing.
    t: Tree = [1]
    print("free value", peek(5))  # tpyc: ok
    print("free alias", bump(t), bump(t))  # tpyc: ok


def method() -> None:
    m = M()
    a = A(1)
    print("method last", m.bump(a))  # tpyc: ok
    print("method fresh", m.bump(A(2)))  # tpyc: ok
    c = A(3)
    print("method copy", m.bump(copy(c)), c.n)  # tpyc: ok


def generic_alias() -> None:
    g = C[A]()
    g.items.append(A(1))
    # Last use of a generic alias's member.
    print("generic last", gbump(g))  # tpyc: ok
    h = C[A]()
    h.items.append(A(2))
    print("generic copy", gbump(copy(h)), h.items[0].n)  # tpyc: ok


def main() -> None:
    free_function()
    method()
    generic_alias()


main()
