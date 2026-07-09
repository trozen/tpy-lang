# Value-repr Optional[bytes] routed through THIR (the bytes twin of Optional[str]):
# the None-test and the return sinks (None -> nullopt, bytes-literal -> owned,
# param -> the view->owned bytes_copy shim). Truthiness/print stay AST (BUGS.md).


def is_absent(b: bytes | None) -> bool:
    return b is None


def pick(keep: bool) -> bytes | None:
    if keep:
        return b"data"
    return None


def forward(b: bytes | None) -> bytes | None:
    return b


def main() -> None:
    print(is_absent(None))
    print(is_absent(b"x"))
    print(pick(True) is None)
    print(pick(False) is None)
    print(forward(b"z") is None)
    print(forward(None) is None)


main()
