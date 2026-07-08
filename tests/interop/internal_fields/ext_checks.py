# Ext-only: the internal-field hiding. On the `.so` a `_`-named field is not a
# Python attribute at all (it stays live C++ payload state); under plain Python
# the same source spells an ordinary attribute, so these assertions hold only
# against the compiled extension -- an acknowledged, unavoidable divergence.
import internal_fields as m

vlt = m.Vault(42, 7)
assert not hasattr(vlt, "_secret"), "internal scalar field _secret leaked to Python"
assert not hasattr(vlt, "_log"), "internal reference-class field _log leaked to Python"
assert not hasattr(vlt, "_cell"), "internal @nocopy field _cell leaked to Python"

# The same holds on an instance that crossed OUT via a return (make_vault).
vault2 = m.make_vault(9, 3)
assert not hasattr(vault2, "_cell"), "internal @nocopy field _cell leaked on a returned instance"

try:
    m.run(-1)
    raise AssertionError("expected OpError")
except m.OpError as e:
    assert e.code == 3, e.code
    # The container internal field never crosses (a public one would be rejected).
    assert not hasattr(e, "_trace"), "internal exception field _trace leaked to Python"
    # Message-only reconstruction, like every data-carrying user exc.
    assert e.args == ("boom",), e.args

try:
    m.run(0)
    raise AssertionError("expected SilentError")
except m.SilentError as e:
    # Only internal fields -> message-only setter; the message still crosses.
    assert str(e) == "silent", repr(str(e))
    assert e.args == ("silent",), e.args
    assert not hasattr(e, "_note"), "internal exception field _note leaked to Python"

print("ext-only internal-field checks: PASS")
