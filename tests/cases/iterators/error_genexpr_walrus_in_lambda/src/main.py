# A generator expression's walrus inside a lambda binds the lambda's own
# variable (PEP 572), which a frame cannot reach: refused, though CPython runs
# it (BUGS.md#genexpr-walrus-lambda-enclosing-scope).
from typing import Callable


def main() -> None:
    # The subject: `y` is read by the lambda after the generator expression.
    g: Callable[[], int] = lambda: sum((y := i) for i in range(3)) + y  # tpyc: error(/assignment expression in a generator expression cannot yet bind 'y' in the enclosing scope; use a for loop or a list comprehension/)
    print(g())


main()
