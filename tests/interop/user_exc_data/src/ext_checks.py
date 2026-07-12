# Ext-only: str(e) and e.args reflect the message field only, not the full
# constructor argument tuple that CPython-native keeps. The C++ exception holds
# typed fields, not the original call tuple, so the boundary reconstructs the
# instance from the message alone (pytype(e.what())) before setting the data
# attributes -- an acknowledged, unavoidable divergence.
import userexcd

try:
    userexcd.parse(-1)
    raise AssertionError("expected ParseError")
except userexcd.ParseError as e:
    assert str(e) == "bad input", repr(str(e))
    assert e.args == ("bad input",), e.args

print("ext-only user-exc data checks: PASS")
