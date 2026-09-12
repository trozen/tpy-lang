# A ternary arm building a tuple with a VIEW-form str member at an
# `Optional[tuple]` element slot: the arm row admits field reads only.
# Concretely, `[(s, v) if c else None]` builds the tuple from a plain str
# param; TPy rejects that shape today.
from typing import Optional
from tpy import int32


def build(s: str, v: int32, c: bool) -> None:
    xs: list[Optional[tuple[str, int32]]] = [(s, v) if c else None]  # tpyc: error(/expr.ifexpr/)
    print(len(xs))


def main() -> None:
    build("hi", 1, True)


main()
