# Error: @dataclass with no field annotations
from dataclasses import dataclass

@dataclass
class Empty:  # tpyc: error(/must have at least one field/)
    pass
