# A top-level `for` whose loop variable shadows an IMPORTED global: the range
# route has no row for a name that already owns a module slot.
# TPy rejects this `for counter in range(0, 3):` loop today.
from tpy import Int32
from gmod import counter


total: Int32 = 0
for counter in range(0, 3):  # tpyc: error(/foreach.var_shadow/)
    total += counter
print(total)
