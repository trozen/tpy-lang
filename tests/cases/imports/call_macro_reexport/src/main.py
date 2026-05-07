# Call-macro re-export through a plain module. utils.py does
# `from dataclasses import dataclass, asdict`; main consumes both
# via utils. Phase 6 originally wired the chain only for class
# decorators; this test guards the call-macro and builder-macro
# paths after the post-Phase-8 review fix.
from utils import dataclass, asdict
from tpy import Int32

@dataclass
class Pair:
    x: Int32
    y: Int32

p = Pair(Int32(3), Int32(4))
print(asdict(p))
