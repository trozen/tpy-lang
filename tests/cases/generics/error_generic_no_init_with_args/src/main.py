# Generic records without __init__ also reject positional constructor args.
from tpy import Int32

class Box[T]:
    val: T

def main() -> None:
    b = Box[Int32](42)  # tpyc: error(/has no __init__/)

main()
