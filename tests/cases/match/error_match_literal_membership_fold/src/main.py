# A membership test over a `Literal` subject inside a match arm: the arm's
# narrowing makes the test a constant, which is folded before lowering sees
# a comparison. TPy rejects this `if mode in (...)` check today.
from typing import Literal


def f(mode: Literal["r", "w", "a"]) -> None:
    match mode:
        case "r" | "w":
            # Inside this arm `mode` is already known to be "r" or "w".
            if mode in ("r", "w"):  # tpyc: error(/stmt\.if:if\.cond_binop\.in\.other_literaltype_tuple:match\.literal_fold/)
                print("rw")
        case "a":
            print("append")


def main() -> None:
    f("r")


main()
