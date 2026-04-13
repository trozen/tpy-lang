# Passing a non-f-string to an FStr parameter should produce a clear error.
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
    m.log(s)  # tpyc: error(/FStr.*only f-string/)

main()
