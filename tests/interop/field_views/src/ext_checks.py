# Ext-only divergences: the view getset is READ-ONLY (a Python-side rebind
# would defeat the never-reassigned gate); holder re-__init__ is rejected
# while views of its fields are alive (the reconstruction would show through
# the slot). Plain Python rebinds attributes and aliases everywhere.
import field_views as m

p = m.Pack(1, 2)
try:
    p.first = m.Cell(5)
    raise AssertionError("expected AttributeError writing a view getset")
except AttributeError:
    pass

try:
    del p.first
    raise AssertionError("expected AttributeError deleting a view getset")
except AttributeError:
    pass

# Holder re-init: rejected while a view is alive; allowed once none are.
v = p.first
try:
    p.__init__(5, 6)
    raise AssertionError("expected TypeError re-initializing under a live view")
except TypeError:
    pass
del v
p.__init__(5, 6)
assert p.first.n == 5

print("ext-only field-view checks: PASS")
