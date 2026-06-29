# Ext-only: the data field does not cross. In the compiled .so the exception is
# a bare PyErr_NewException type with no `line` attribute, while the TPy source
# under CPython (cpy-parity) carries it -- so the absence is asserted only
# against the .so.
import userexcd

try:
    userexcd.parse(-1)
    raise AssertionError("expected ParseError")
except userexcd.ParseError as e:
    assert str(e) == "bad input"
    assert not hasattr(e, "line"), "data field unexpectedly crossed the boundary"

print("ext-only user-exc data checks: PASS")
