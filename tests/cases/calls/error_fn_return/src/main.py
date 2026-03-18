# Fn type cannot be used as return type.
from tpy import Fn, Int32

def bad() -> Fn[[Int32], Int32]:  # tpyc: error(/Fn type is only valid in parameter position/)
    pass
