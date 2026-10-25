# A constructor's explicit type arguments are checked against the class's
# parameter kinds, as an annotation's are: a type at an integer parameter
# (docs/LANGUAGE_FEATURES.md "Integer Type Parameters").


class U[N: int]:
    pass


def main() -> None:
    U[str]()  # tpyc: error(/Type parameter 'N' of 'U' requires an integer, got str/)


main()
