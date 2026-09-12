# A for-each UNPACK of `tuple[bytes | None, str | None]` elements: the narrowed
# optional-view element reads through a deref binding inside the loop body. No
# element literal of that tuple type can be spelled today (a tuple literal with
# an optional element rejects at expr.tuple_literal), so the rows stay empty
# and the pin is the loop body's render.
from tpy import int32


def sink(v: str | None) -> int32:
    return 1 if v is not None else 0


def use(rows: list[tuple[bytes | None, str | None]]) -> int32:
    n = 0
    for body, ctype in rows:  # the unpacked optional-view elements
        if ctype is not None:
            n += len(ctype)  # the narrowed element deref
        n += sink(ctype)
    return n


def main() -> None:
    rows: list[tuple[bytes | None, str | None]] = []
    print(use(rows))


main()
