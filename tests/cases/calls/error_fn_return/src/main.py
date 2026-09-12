# Fn type cannot be used as return type.
from tpy import Fn, int32

def bad() -> Fn[[int32], int32]:  # tpyc: error(/Fn type is only valid in parameter position/)
    pass
