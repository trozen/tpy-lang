# Test UninitHeapStorage move semantics: return by Own and pass by Own.
from tpy import Int32, Own
from tpy.mem import UninitHeapStorage

# Move via return (NRVO or move construction)
def make_storage() -> Own[UninitHeapStorage[Int32]]:
    s = UninitHeapStorage[Int32](4)
    s.init(0, 100)
    s.init(1, 200)
    return s

storage = make_storage()
print(storage.load(0))
print(storage.load(1))
storage.drop(0)
storage.drop(1)

# Move via Own parameter (auto-move at last use)
def consume(s: Own[UninitHeapStorage[Int32]]) -> Int32:
    val: Int32 = s.load(0)
    s.drop(0)
    return val

def test_pass_own() -> None:
    s2 = UninitHeapStorage[Int32](2)
    s2.init(0, 300)
    print(consume(s2))

test_pass_own()
