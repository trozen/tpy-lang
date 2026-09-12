# A short-name-only cross-module guard would wrongly treat the foreign
# `ForeignFoo` (imported from foo_mod, aliased) as local here because this
# module also defines its own unrelated @export class named `Foo` -- the
# guard must resolve by qualified identity, not by bare name.
# tpy: ext_module
from tpy import int64
from tpy.extern import export
from foo_mod import Foo as ForeignFoo


@export
class Foo:
    def __init__(self, value: int64):
        self.value = value


@export
def use_foreign(f: ForeignFoo) -> int64:  # tpyc: error(/is an exposed class from another module/)
    return f.get()
