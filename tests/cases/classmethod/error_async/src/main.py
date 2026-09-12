# An async @classmethod is not supported yet (mirrors the @staticmethod
# rejection) and must name the decorator the user actually wrote.
from tpy import int32


class P:
    @classmethod
    async def fetch(cls) -> int32:  # tpyc: error(/async @classmethod is not yet supported/)
        return 1


def main() -> None:
    print(1)


main()
