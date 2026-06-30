# Only top-level module enums are exposed; a @export enum nested inside a class
# is rejected (rather than silently dropped from the glue) until nested exposure
# is designed.
# tpy: ext_module
from enum import IntEnum
from tpy.extern import export


class Outer:
    @export
    class Inner(IntEnum):  # tpyc: error(/nested enum cannot be exposed/)
        A = 1
        B = 2
