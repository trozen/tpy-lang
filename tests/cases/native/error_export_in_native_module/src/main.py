# Test that @export in a native module ("mod": "native") produces an error
__tpy__ = {"mod": "native"}
from tpy.extern import export
from tpy import int32

@export(binding="C")
def bad_func(x: int32) -> int32:  # tpyc: error(/not allowed in a native module/)
    return x
