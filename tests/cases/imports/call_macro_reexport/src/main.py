# Call-macro re-export through a plain module. utils.py does
# `from dataclasses import dataclass, asdict`; main consumes both
# via utils. Phase 6 originally wired the chain only for class
# decorators; this test guards the call-macro and builder-macro
# paths after the post-Phase-8 review fix.
from utils import dataclass, asdict
from tpy import int32

@dataclass
class Pair:
    x: int32
    y: int32

p = Pair(int32(3), int32(4))
print(asdict(p))
