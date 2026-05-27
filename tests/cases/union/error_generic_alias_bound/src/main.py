# PEP 695 bound syntax on alias type params is rejected at parse time.
# v1 of generic recursive aliases keeps bounds out of scope.

type Tree[T: int] = T  # tpyc: error(/bounds on type parameters .* are not yet supported/)


def main() -> None:
    print("never reached")


main()
