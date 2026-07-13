# Ext-only: the acknowledged hash divergence in its inherited-custom-hash
# form -- LeafNode adds only __lt__, which populates its own tp_richcompare,
# and PyType_Ready then nulls its hash; plain Python keeps LeafNode hashable
# through Node's inherited custom __hash__ (only __eq__ unsets hash there).
import class_inheritance_slots as m

try:
    hash(m.LeafNode(1))
    raise AssertionError("expected TypeError hashing LeafNode")
except TypeError:
    pass

print("ext-only slot-inheritance checks: PASS")
