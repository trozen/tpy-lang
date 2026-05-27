# Duplicate type parameter names in a generic alias declaration are
# rejected at parse time, matching CPython's own rejection of the same
# PEP 695 form.

type Pair[T, T] = tuple[T, T]  # tpyc: error(/duplicate type parameter name 'T'/)


def main() -> None:
    pass


main()
