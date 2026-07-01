# Same rejection as the cross-module param case, but for an @export
# function's return type.
# tpy: ext_module
from tpy import Int64, Own
from tpy.extern import export
from foo_mod import Foo


@export
def make_foo(value: Int64) -> Own[Foo]:  # tpyc: error(/is an exposed class from another module/)
    return Foo(value)
