# A builtin-module constructor that resolves a GENERIC overload keeps the
# marker carve-out's "no type params" guard, so the body rejects.
import tpy


def widen() -> None:
    x: tpy.int32 = tpy.int32(10)
    y: tpy.int32 = tpy.int32(tpy.uint32(5))  # tpyc: error(/method.marker.builtin_module.ctor/)
    print(x, y)


def main() -> None:
    widen()


main()
