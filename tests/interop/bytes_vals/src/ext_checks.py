# Ext-only: the compiled extension enforces the declared `bytes` type at the
# boundary (PyBytes_AsStringAndSize), while the TPy source leaves the annotation
# unchecked. A non-bytes argument is a TypeError -- notably a `str` and a
# `bytearray` (a mutable buffer, not accepted by value) -- only against the
# built .so.
import bytes_vals as m


def expect(exc, f):
    try:
        f()
    except exc:
        return "ok"
    return "NO-RAISE"


assert expect(TypeError, lambda: m.echo("str")) == "ok"            # str is not bytes
assert expect(TypeError, lambda: m.echo(bytearray(b"x"))) == "ok"  # mutable buffer rejected
assert expect(TypeError, lambda: m.echo(123)) == "ok"
assert expect(TypeError, lambda: m.cat(b"a", "b")) == "ok"         # second arg not bytes

print("ext-only bytes boundary checks: PASS")
