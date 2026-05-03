# Empty subclass of an @native class whose C++ name differs from its Python
# name. The inherited 'using <Base>::<Base>;' has to use the C++ short name
# for the constructor; using the Python class name would emit invalid C++.
# tpy: include("native_types.hpp")
from tpy.extern import native
from tpy import Int32

@native("CppCounter")
class PyCounter:
    value: Int32

    @native("CppCounter", function=True)
    def __init__(self, value: Int32) -> None: ...

    def get(self) -> Int32: ...


class Sub(PyCounter):
    pass


def main() -> None:
    s = Sub(Int32(7))
    print(s.get())


main()
