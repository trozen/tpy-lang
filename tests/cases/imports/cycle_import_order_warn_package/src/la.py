# CPython loads the whole from-list before it binds any name of it: loading
# the submodule `rp.y` runs it while `la` has bound nothing yet.
from rp import X, y


def main() -> None:
    print("from-list:", X(5).v, y.f())
