# float32() takes its value positionally only, as float() does in CPython
# (TypeError: float() takes no keyword arguments).
from tpy import float32


def main() -> None:
    print(float32(x=1.5))  # tpyc: error(/No matching overload for 'float32' with argument types \(x=float\)/)


main()
