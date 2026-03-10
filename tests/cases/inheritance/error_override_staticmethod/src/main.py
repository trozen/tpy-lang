# @override combined with @staticmethod is invalid -- static methods aren't overrides.
from tpy import Int32
from typing import override

class Base:
    @staticmethod
    def helper() -> Int32:
        return Int32(0)


class Child(Base):
    @override
    @staticmethod
    def helper() -> Int32:  # tpyc: error(/cannot be combined with @staticmethod/)
        return Int32(1)
