# A method set whose variants differ only by what the const inference finds:
# neither is declared @readonly, so neither is ranked as readonly.
from tpy import dispatch, int32, int64


class Inferred:
    hits: int32

    def __init__(self) -> None:
        self.hits = 0

    @dispatch
    def m(self, k: int64) -> int32:
        self.hits += 1
        return 200

    @dispatch
    def m(self, k: int32) -> int32:
        return k + 100
