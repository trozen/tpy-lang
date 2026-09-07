# A builtin-module constructor that resolves a GENERIC overload keeps the
# marker carve-out's "no type params" guard, so the body rejects.
import tpy


def widen() -> None:
    x: tpy.Int32 = tpy.Int32(10)
    y: tpy.Int32 = tpy.Int32(tpy.UInt32(5))  # tpyc: error(/method.marker.builtin_module.ctor/)
    print(x, y)


def main() -> None:
    widen()


main()
