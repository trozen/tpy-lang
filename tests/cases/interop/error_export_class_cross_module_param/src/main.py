# A foreign exposed class used as an @export function param is a located
# error, not a misbuilt .so (mirrors the enum cross-module rejection).
# tpy: ext_module
from tpy import int64
from tpy.extern import export
from foo_mod import Foo


@export
def use_foo(f: Foo) -> int64:  # tpyc: error(/is an exposed class from another module/)
    return f.get()
