# Inverse of error_bytearray_return_by_value: Own[bytearray] returns the fresh
# buffer by value (move), and a bytearray PARAM may be returned by reference.
from tpy import Own

def make() -> Own[bytearray]:
    return bytearray(b"hi")

def first_param(b: bytearray) -> bytearray:
    return b

def main() -> None:
    print(len(make()))
    ba = bytearray(b"abc")
    print(len(first_param(ba)))

main()
