# Shared across the ext-exec and cpy-parity runs: bytes values that round-trip
# identically through the compiled extension and the TPy source under CPython.
# bytes is immutable, so the boundary copy is unobservable and every value
# agrees; non-bytes arguments diverge and live in ext_checks.py.
import bytes_vals as m

print(m.echo(b"hello"))
print(m.echo(b""))                  # empty
print(m.echo(b"\x00\x01\xff"))      # raw bytes incl NUL and a high byte
print(m.cat(b"ab", b"cd"))
print(m.cat(b"", b"xy"))
print(m.shout(b"hello"))
print(m.make_own())                 # Own[bytes] return admits and round-trips
