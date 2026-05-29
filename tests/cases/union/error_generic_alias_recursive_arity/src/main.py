# A generic recursive alias whose self-reference has the wrong arity is
# rejected at the parse-resolution self-ref site (1 declared, 2 given).

# tpyc: error(/Recursive alias 'Tree' self-reference takes 1 type argument, got 2/)
type Tree[T] = T | list[Tree[T, T]]


def main() -> None:
    print("never reached")


main()
