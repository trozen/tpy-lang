# Ext-only: the keyword-dispatch error paths PyArg_ParseTupleAndKeywords
# rejects -- unknown keyword and a param passed both positionally and by
# keyword. Asserted on the exception TYPE only: the C-API parser's message
# wording differs from CPython's own (see docs/CPYTHON_INTEROP.md), so matching
# str(e) would be host/impl-fragile. These run against the built .so only.
import kwargs


def expect(exc, f):
    try:
        f()
    except exc:
        return "ok"
    return "NO-RAISE"


# Unknown keyword -- on a free function, a method, and the constructor.
assert expect(TypeError, lambda: kwargs.total(a=1, b=2, z=3)) == "ok"
v = kwargs.Vec(1, 2)
assert expect(TypeError, lambda: v.move(dx=1, dz=2)) == "ok"
assert expect(TypeError, lambda: kwargs.Vec(x=1, z=2)) == "ok"

# Same parameter passed both positionally and by keyword.
assert expect(TypeError, lambda: kwargs.total(1, a=2, c=3)) == "ok"

print("ext-only kwargs error checks: PASS")
