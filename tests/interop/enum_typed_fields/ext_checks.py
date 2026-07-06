# Ext-only: the compiled getset setter is strict by-type, where the untyped
# source class would store whatever it's given. A bare int (even a valid enum
# value) and a str are both rejected with TypeError; the field is unchanged.
import enum_typed_fields as m

w = m.Widget(m.Color.RED)

try:
    w.color = 2                     # a valid value, but not a member
    raise AssertionError("expected TypeError setting enum field to a bare int")
except TypeError:
    pass

try:
    w.color = "green"
    raise AssertionError("expected TypeError setting enum field to a str")
except TypeError:
    pass

assert w.color is m.Color.RED       # unchanged after the rejected sets

print("ext-only enum-field checks: PASS")
