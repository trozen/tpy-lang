# A value-repr Optional[str]/Optional[bytes] local (None-init, reassigned in a
# branch): the `is None` test and the narrowed deref into an owned slot.


def pick_str(argv: list[str]) -> str:
    acc: str | None = None
    for tok in argv:
        if tok == "cmd":
            acc = tok
            break
    if acc is None:
        return "<none>"
    chosen: str = acc
    return chosen


def pick_bytes(n: int) -> int:
    # Owned bytes literal source: reassigning a bytes VIEW into an owned
    # optional<vector> is a separate pre-existing AST bug, not this test's
    # subject -- here we exercise the None-test + narrowed deref of the local.
    acc: bytes | None = None
    if n > 0:
        acc = b"hello"
    assert acc is not None
    head: bytes = acc
    return len(head)


def main():
    print(pick_str(["a", "cmd", "b"]))
    print(pick_str(["x", "y"]))
    print(pick_bytes(1))


main()
