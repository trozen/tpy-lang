# Closure frames behind Send[Callable]: no-capture lambdas, by-value
# captures of Send types, free-function refs, and nested defs qualify.
# Note: Callable closures capture by value, so output matches CPython only
# because nothing mutates a captured var after capture (see BUGS.md: silent
# capture-by-value divergence). A mutate-after-capture test would diverge.
from tpy import Int32, Send
from typing import Callable

def take(cb: Send[Callable[[Int32], None]]) -> None:
    cb(1)

def free_fn(n: Int32) -> None:
    print("free", n)

def main() -> None:
    take(lambda n: print("lam", n))
    k = 10
    take(lambda n: print("cap", n + k))
    take(free_fn)
    xs = [1, 2]
    take(lambda n: print("list", n + xs[0]))

    def nested(n: Int32) -> None:
        print("nested", n + k)
    take(nested)

main()
