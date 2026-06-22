# An Optional[Callable] param is not callable until narrowed: calling it
# without an `is not None` guard must be rejected (it may be None).
from typing import Callable
from tpy import Int32


def run(hook: Callable[[Int32], None] | None = None) -> None:
    hook(1)   # tpyc: error(/not callable/)


def main() -> None:
    run()


main()
