# A classmethod cannot be a generator (mirrors the @staticmethod rejection).
from typing import Iterator

from tpy import int32


class P:
    @classmethod
    def counts(cls) -> Iterator[int32]:  # tpyc: error(/@classmethod method 'counts' cannot be a generator/)
        yield 1


def main() -> None:
    for n in P.counts():
        print(n)


main()
