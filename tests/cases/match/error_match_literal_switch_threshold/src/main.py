# Five unguarded literal alternatives over a `Literal` subject: the count
# alone selects the discriminator switch, whose render against a Literal
# subject is its own row. TPy rejects this five-arm `match m:` statement today.
from typing import Literal


def f(m: Literal["a", "b", "c", "d", "e"]) -> None:
    # Five arms is the threshold that picks the switch strategy.
    match m:  # tpyc: error(/stmt\.match/)
        case "a":
            print(1)
        case "b":
            print(2)
        case "c":
            print(3)
        case "d":
            print(4)
        case "e":
            print(5)


def main() -> None:
    f("a")


main()
