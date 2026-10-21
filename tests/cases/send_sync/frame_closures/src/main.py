# Closure frames behind Send[Callable]: no-capture lambdas, by-value
# captures of Send types, free-function refs, and nested defs qualify.
# Note: Callable closures capture by value, so output matches CPython only
# because nothing reassigns or mutates a captured var after capture. A
# reassign-after-capture now warns (see nested_def/escaping_value_capture_
# reassigned), and so does a copied reference-type capture, whose in-place
# mutation the closure would not see.
from tpy import int32, Send
from typing import Callable

def take(cb: Send[Callable[[int32], None]]) -> None:
    cb(1)

def free_fn(n: int32) -> None:
    print("free", n)

def main() -> None:
    take(lambda n: print("lam", n))
    k = 10
    take(lambda n: print("cap", n + k))
    take(free_fn)
    xs = [1, 2]
    # the lambda copies `xs`; nothing mutates it, so the output matches
    take(lambda n: print("list", n + xs[0]))  # tpyc: warning(/copies the object 'xs'/)

    def nested(n: int32) -> None:
        print("nested", n + k)
    take(nested)

main()
