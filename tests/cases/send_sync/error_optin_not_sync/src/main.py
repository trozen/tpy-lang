# class Foo(Sync) is a checked claim: a mutable-container field fails it.
from tpy import Int32, Sync

class Bad(Sync):  # tpyc: error(/declares Sync but field 'xs: list\[Int32\]' is not Sync/)
    xs: list[Int32]
