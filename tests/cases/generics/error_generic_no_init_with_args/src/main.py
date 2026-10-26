# Generic records without __init__ also reject positional constructor args,
# with the message a non-generic one and a raise give.
from tpy import int32

class Box[T]:
    val: T

def main() -> None:
    b = Box[int32](42)  # tpyc: error(/has no __init__; use @dataclass/)

main()
