# D157 regression: a generic record from one module must NOT unify with a
# same-short-named generic record from another during type-param inference.
# `first[T](b: Box[T])` is declared over ga.Box; passing a gb.Box (imported as
# BoxB) must not bind T from the foreign instantiation -- inference finds no
# match and rejects, rather than silently treating gb.Box as ga.Box.
from ga import Box
from gb import Box as BoxB


def first[T](b: Box[T]) -> T:
    return b.v


def main() -> None:
    print(first(BoxB(9)))  # tpyc: error(/[Cc]annot infer type arguments/)


main()
