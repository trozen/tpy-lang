# A comprehension storing a @nocopy reference element into the owned result
# container is a clean compile error (not a silent g++ failure) -- the element
# routes through the same per-element copy check as the literal/append sinks.
from tpy import int32
from tplib.box import Box


def f(items: list[Box[int32]]) -> None:
    xs: list[Box[int32]] = [b for b in items]  # tpyc: error(/cannot copy non-copyable type 'Box\[int32\]' into owned storage/)
    print(len(xs))


def main() -> None:
    f([Box(1)])


main()
