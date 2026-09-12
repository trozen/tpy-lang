# @override marks an instance-method override; a classmethod has no receiver
# to dispatch on (mirrors the @override + @staticmethod rejection).
from typing import override

from tpy import int32


class Base:
    @classmethod
    def make(cls) -> int32:
        return 1


class Child(Base):
    @override
    @classmethod
    def make(cls) -> int32:  # tpyc: error(/@override cannot be combined with @classmethod/)
        return 2


def main() -> None:
    print(Child.make())


main()
