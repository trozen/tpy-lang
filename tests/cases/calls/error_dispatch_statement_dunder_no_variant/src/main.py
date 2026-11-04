# The call a statement makes implicitly resolves like a written one: when no
# variant of an `__enter__` @dispatch set takes `__enter__()`, the `with` is
# an error naming that call.
from tpy import dispatch, int32


class Manager:
    def __init__(self) -> None:
        pass

    @dispatch
    def __enter__(self, x: int32) -> int32:
        return x

    @dispatch
    def __enter__(self, x: str) -> int32:
        return len(x)

    def __exit__(self, kind: None, value: None, tb: None) -> None:
        pass


def main() -> None:
    m = Manager()
    with m as v:  # tpyc: error(/no variant of `__enter__` accepts the call the statement makes: `__enter__\(\)`/)
        print(v)


main()
