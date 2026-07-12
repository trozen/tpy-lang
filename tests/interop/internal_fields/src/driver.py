# Shared across the ext-exec and cpy-parity runs: public fields cross, internal
# `_`-fields are reached only through methods, and a data-carrying exception's
# public field crosses while its internal field is hidden. Every line here is
# source-parity -- the driver never reads a `_`-attribute (that hiding is the
# acknowledged divergence, asserted ext-only in ext_checks.py).
import internal_fields as m

vlt = m.Vault(42, 7)
print(vlt.owner)              # public getset
print(vlt.reveal())          # internal scalar, read through a method
print(vlt.snapshot().v)      # internal reference member, copied out via a method

vlt.record(100)              # mutate the internal member through self
print(vlt.snapshot().v)      # the mutation persists -- it is live payload state

vault2 = m.make_vault(9, 3)  # Vault (move-only via its @nocopy member) crosses OUT
print(vault2.owner)          # public getset on the returned instance
print(vault2.cell_n())       # read the @nocopy internal member through a method

print(m.run(5))

try:
    m.run(-1)
    raise AssertionError("expected OpError")
except m.OpError as e:
    print(type(e).__name__)
    print(isinstance(e, ValueError))
    print(e.code)            # public data field crosses

try:
    m.run(0)
    raise AssertionError("expected SilentError")
except m.SilentError as e:   # all fields internal -> message-only, still its type
    print(type(e).__name__)
    print(isinstance(e, ValueError))
