# Shared across the ext-exec and cpy-parity runs: str values that round-trip
# identically through the compiled extension and the TPy source under CPython.
# The boundary copy is unobservable (str is immutable), so every value agrees;
# non-str and lone-surrogate arguments diverge and live in ext_checks.py.
import strings

print(repr(strings.echo("hello")))
print(repr(strings.echo("")))                       # empty
print(repr(strings.echo("a\x00b")))                 # embedded NUL: length-based, not C-string
print(repr(strings.echo("caf\u00e9 \u4e2d")))  # multibyte UTF-8 round-trips
print(repr(strings.shout("hello")))
print(repr(strings.shout("")))
print(repr(strings.greet("world")))
print(repr(strings.greet("")))
