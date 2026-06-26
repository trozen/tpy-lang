# Ext-only: the compiled extension enforces each declared width at the boundary
# (out-of-range -> OverflowError, negative-into-unsigned -> OverflowError,
# non-integer -> TypeError), while the TPy source treats the annotation as an
# unbounded, unchecked `int`. So these deliberately diverge and are checked only
# against the built .so.
import int_widths as m


def expect(exc, f):
    try:
        f()
    except exc:
        return "ok"
    return "NO-RAISE"


# Out-of-range for the signed widths -- both ends of each.
assert expect(OverflowError, lambda: m.i8(128)) == "ok"
assert expect(OverflowError, lambda: m.i8(-129)) == "ok"
assert expect(OverflowError, lambda: m.i16(32768)) == "ok"
assert expect(OverflowError, lambda: m.i16(-32769)) == "ok"
assert expect(OverflowError, lambda: m.i32(2**31)) == "ok"
assert expect(OverflowError, lambda: m.i32(-(2**31) - 1)) == "ok"
assert expect(OverflowError, lambda: m.i64(2**63)) == "ok"
assert expect(OverflowError, lambda: m.i64(-(2**63) - 1)) == "ok"

# Out-of-range / negative for the unsigned widths -- overflow and negative each.
assert expect(OverflowError, lambda: m.u8(256)) == "ok"
assert expect(OverflowError, lambda: m.u8(-1)) == "ok"
assert expect(OverflowError, lambda: m.u16(65536)) == "ok"
assert expect(OverflowError, lambda: m.u16(-1)) == "ok"
assert expect(OverflowError, lambda: m.u32(2**32)) == "ok"
assert expect(OverflowError, lambda: m.u32(-1)) == "ok"
assert expect(OverflowError, lambda: m.u64(2**64)) == "ok"
assert expect(OverflowError, lambda: m.u64(-1)) == "ok"

# A non-integer has no __index__ -> TypeError, same as the Int64 rung.
assert expect(TypeError, lambda: m.i32(1.5)) == "ok"
assert expect(TypeError, lambda: m.u64("x")) == "ok"

print("ext-only int-width range checks: PASS")
