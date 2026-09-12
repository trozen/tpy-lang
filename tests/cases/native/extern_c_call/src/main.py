from tpy.extern import export
from tpy import int32

# Export with explicit C name — verifies renamed call codegen
@export("Helper_Add", binding="C")
def helper_add(x: int32) -> int32:
    return x + int32(1)

# Export that calls the renamed function above.
# Calling helper_add() must emit Helper_Add() in C++.
@export(binding="C")
def app_init() -> None:
    y: int32 = helper_add(int32(42))
    print(y)
