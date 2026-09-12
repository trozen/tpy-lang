# Cross-module: generic class with class constant in `box.py`, accessed via
# instance from `main.py`. Codegen must qualify both the namespace
# (`tpyapp::box`) and the type-args (`<int32_t>`) at the access site.
from box import Box
from tpy import int32


def main() -> None:
    b = Box[int32]()
    print(b.CAPACITY)
    c = Box[float]()
    print(c.CAPACITY)


main()
