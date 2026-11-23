# A binding's `Ptr` result may point into the opaque native object it is
# given: returned over a local object it is refused.
from tpy import int32, Ptr
from tpy.extern import native, cpp_template


@native
class Opaque:
    def __init__(self) -> None: ...


@cpp_template("{0}.data()")
def data(x: Opaque) -> Ptr[int32]: ...


def leak() -> Ptr[int32]:
    ob = Opaque()
    # the pointer would point into `ob`, which dies here
    return data(ob)  # tpyc: error(/Cannot return pointer to local or temporary value/)


def main() -> None:
    p = leak()


main()
