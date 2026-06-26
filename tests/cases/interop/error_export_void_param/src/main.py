# A None/void type is valid only as an @export *return*; as a parameter there is
# no host value to unmarshal, so it is rejected like any unmarshallable type.
# tpy: ext_module
from tpy.extern import export


@export
def f(x: None) -> int:  # tpyc: error(/parameter 'x'.*not yet marshallable/)
    return 0
