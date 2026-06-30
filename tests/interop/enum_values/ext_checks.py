# Ext-only: the enum-value boundary is strict by type -- an enum param requires
# the exact enum member, so a bare int (even one whose value matches a member) or
# a different enum type raises TypeError. The plain Python source treats the
# annotation as a hint and accepts any value (e.g. next_color(1) returns
# Color.GREEN), so this is checked only against the built .so, never in cpy-parity.
import enum_values as ev


def expect_typeerror(f):
    try:
        f()
        return "no-error"
    except TypeError:
        return "ok"


assert expect_typeerror(lambda: ev.next_color(1)) == "ok"               # bare int
assert expect_typeerror(lambda: ev.next_color(ev.Shape.CIRCLE)) == "ok"  # wrong enum type
assert expect_typeerror(lambda: ev.flip(1)) == "ok"
