# Test aliased imports: from tpy.mem import UninitArrayStorage as U
from tpy import Int32
from tpy.mem import UninitArrayStorage as U
from tpy.mem import UninitHeapStorage as H

# Aliased array storage
a = U[Int32, 2]()
a.init(0, 10)
a.init(1, 20)
print(a.load(0))
print(a.load(1))
a.drop(0)
a.drop(1)

# Aliased heap storage
h = H[Int32](2)
h.init(0, 30)
print(h.load(0))
h.drop(0)
