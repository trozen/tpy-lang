from a import a_func
from util import boost
from tpy import Int32

def b_func() -> Int32:
    return boost(7)

def b_helper() -> Int32:
    return 3

# Closes the cycle via real usage: b_func_via_a calls into a's
# function, which in turn calls b_helper above. Without this, the
# `from a import a_func` line above would be a dead import and the
# cycle would be registered solely on the import-statement edge.
def b_func_via_a() -> Int32:
    return a_func()
