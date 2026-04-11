from tpy.extern import export
from tpy import Int32

# Export with explicit C name — verifies renamed call codegen
@export("Helper_Add", binding="C")
def helper_add(x: Int32) -> Int32:
    return x + Int32(1)

# Export that calls the renamed function above.
# Calling helper_add() must emit Helper_Add() in C++.
@export(binding="C")
def app_init() -> None:
    y: Int32 = helper_add(Int32(42))
    print(y)
