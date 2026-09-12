from tpy import int32


class Code:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def compile_pattern(s: str) -> int32:
    return int32(len(s))
