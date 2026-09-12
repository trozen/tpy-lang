# random.seed() / Random(None) auto-seed from OS entropy. Entropy is
# non-deterministic so this test only verifies sanity:
# - calls succeed without panicking
# - successive calls produce different streams (with overwhelming probability)
# - generated values stay in their advertised ranges
import random
from random import Random
from tpy import int32

def main() -> None:
    # Module-level seed() with no arg: re-seed from entropy.
    random.seed()
    r1: float = random.random()
    random.seed()
    r2: float = random.random()
    # Two independent entropy seeds will essentially never collide on
    # the first random() value (probability ~2^-53).
    print("auto_seed_distinct:", r1 != r2)
    print("in_unit:", 0.0 <= r1 < 1.0 and 0.0 <= r2 < 1.0)

    # Random(None) constructor auto-seeds. Each instance independent.
    a: Random = Random(None)
    b: Random = Random(None)
    print("instance_distinct:", a.random() != b.random())

    # Default constructor (no arg) also auto-seeds.
    c: Random = Random()
    v: float = c.random()
    print("default_in_unit:", 0.0 <= v < 1.0)

main()
