# A non-generic @value_ptr_coercion callee takes a value at its Ptr
# parameter by address, as the generic one (`take_ptr[T]`) does: the
# receive reads the coercion off the callee, whatever the call path.
from tpy import Ptr, int32
from tpy.extern import cpp_template, value_ptr_coercion


@value_ptr_coercion
@cpp_template("(*{0} += 1)")
def bump(p: Ptr[int32]) -> int32: ...


def main() -> None:
    x: int32 = 5
    # `x` is passed by address and bumped in place
    y = bump(x)  # tpyc: ok
    print("bumped:", y, x)


main()
