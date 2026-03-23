# Tests iter() at module level (global scope) -- the result is a structural
# protocol type which requires decltype() in the C++ global declaration.
# Also tests reassignment of a protocol-typed global.

d = {"a": 1, "b": 2, "c": 3}
it = iter(d)

def use_global_iter() -> None:
    for k in it:
        print(k)

use_global_iter()

nums = [10, 20, 30]
it2 = iter(nums)
for v in it2:
    print(v)

# Reassign protocol-typed global to a new iterator
d2 = {"x": 10, "y": 20}
it = iter(d2)
for k in it:
    print(k)
