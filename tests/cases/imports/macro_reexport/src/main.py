# Macro re-export through a plain (non-facade) module. utils.py does
# `from dataclasses import dataclass`; main pulls @dataclass via utils
# and applies it. Phase 6 of the per-module attribute table refactor
# enables this -- the parser canonicalizes the decorator's source
# module to the ultimate macro definer (dataclasses), and sema's
# _apply_class_macros walks the binding chain when the parser's
# immediate-source qname misses in MacroRegistry.
from utils import dataclass

@dataclass
class Point:
    x: int
    y: int

p = Point(3, 4)
print(p.x, p.y)
