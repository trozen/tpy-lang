# Ext-only: the compiled extension enforces the declared `str` type at the
# boundary (PyUnicode_AsUTF8AndSize), while the TPy source leaves the annotation
# unchecked. A non-str argument is a TypeError, and a lone-surrogate str has no
# strict-UTF-8 encoding so it is a UnicodeEncodeError -- both only against the
# built .so.
import strings


def expect(exc, f):
    try:
        f()
    except exc:
        return "ok"
    return "NO-RAISE"


# Non-str arguments have no UTF-8 to borrow -> TypeError.
assert expect(TypeError, lambda: strings.echo(123)) == "ok"
assert expect(TypeError, lambda: strings.echo(b"bytes")) == "ok"   # bytes is not str
assert expect(TypeError, lambda: strings.shout(None)) == "ok"

# A lone surrogate cannot be encoded as strict UTF-8 at the boundary.
assert expect(UnicodeEncodeError, lambda: strings.echo("\udc80")) == "ok"

print("ext-only str boundary checks: PASS")
