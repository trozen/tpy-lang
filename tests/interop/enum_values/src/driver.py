# Shared across the ext-exec and cpy-parity runs: an enum value passed to/from an
# @export function must behave like the plain Python source -- the returned member
# is the module singleton (identity preserved), with the right name/value/type.
# Only member args are used here; the non-member-arg divergence is ext-only.
import enum_values as ev

print(repr(ev.next_color(ev.Color.RED)))               # <Color.GREEN: 2>
print(ev.next_color(ev.Color.RED) is ev.Color.GREEN)   # True (singleton identity)
print(repr(ev.next_color(ev.Color.GREEN)))             # <Color.RED: 1>
print(repr(ev.next_color(ev.Color.INVALID)))           # <Color.INVALID: -1> (negative)
r = ev.next_color(ev.Color.GREEN)
print(r.name, r.value, isinstance(r, ev.Color))        # RED 1 True

print(repr(ev.flip(ev.Shape.CIRCLE)))                  # <Shape.SQUARE: 2>
print(ev.flip(ev.Shape.CIRCLE) is ev.Shape.SQUARE)     # True
print(repr(ev.flip(ev.Shape.SQUARE)))                  # <Shape.CIRCLE: 1>

# UInt64 member above INT64_MAX must round-trip unsigned (not wrap negative)
print(ev.echo_big(ev.Big.HIGH).value)                  # 9223372036854775808
print(ev.echo_big(ev.Big.HIGH) is ev.Big.HIGH)         # True
print(ev.echo_big(ev.Big.LOW) is ev.Big.LOW)           # True

# an enum also crosses as a method param/return
t = ev.Toggle(5)
print(repr(t.pick(ev.Color.RED)))                      # <Color.GREEN: 2>
print(t.pick(ev.Color.GREEN) is ev.Color.RED)          # True

# asymmetric: enum-IN-only (scalar return) and enum-OUT-only (no enum param)
print(ev.is_red(ev.Color.RED), ev.is_red(ev.Color.GREEN))  # True False
print(ev.default_color() is ev.Color.GREEN)                # True
