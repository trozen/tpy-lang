# FStr method with non-single-call body should produce a clear error.
from tpy import FStr, inline


class Module:
    _logger: str

    def __init__(self, name: str) -> None:
        self._logger = name

    @inline
    def log(self, fs: FStr) -> None:  # tpyc: error(/@inline.*single call/)
        x = 1
        print(x)

def main() -> None:
    pass

main()
