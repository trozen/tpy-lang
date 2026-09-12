# @override combined with @staticmethod is invalid -- static methods aren't overrides.
from tpy import int32
from typing import override

class Base:
    @staticmethod
    def helper() -> int32:
        return int32(0)


class Child(Base):
    @override
    @staticmethod
    def helper() -> int32:  # tpyc: error(/cannot be combined with @staticmethod/)
        return int32(1)
