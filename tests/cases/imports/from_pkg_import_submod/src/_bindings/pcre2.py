from tpy import Int32


class Code:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


def compile_pattern(s: str) -> Int32:
    return Int32(len(s))
