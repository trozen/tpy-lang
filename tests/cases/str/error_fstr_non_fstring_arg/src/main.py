# Passing a string variable (not a literal) to an FStr parameter should error.
from tpy import FStr, inline


def log_impl(fmt: str) -> None:
    print(fmt)


class Module:
    _logger: str

    def __init__(self, name: str) -> None:
        self._logger = name

    @inline
    def log(self, fs: FStr) -> None:
        log_impl(fs)


def main() -> None:
    m = Module("M")
    s = "hello"
    m.log(s)  # tpyc: error(/FStr.*string literals are accepted/)

main()
