# Bound references a type parameter declared later; rejected at
# registration with a precise diagnostic instead of the context-less
# "Unknown type parameter 'V'" from substitute_type_params downstream.
from __future__ import annotations
from tpy import Own


def f[U: T, T](value: Own[U]) -> Own[T]:  # tpyc: error(/Type parameter 'U' references 'T' in its bound, but 'T' is declared later/)
    return value


def main() -> None:
    pass


main()
