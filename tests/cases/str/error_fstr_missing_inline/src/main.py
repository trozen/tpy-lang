# FStr parameter without @inline should produce a clear error.
from tpy import FStr


class Module:
    _logger: str

    def __init__(self, name: str) -> None:
        self._logger = name

    def log(self, fs: FStr) -> None:  # tpyc: error(/FStr.*@inline/)
        print(fs)

def main() -> None:
    pass

main()
