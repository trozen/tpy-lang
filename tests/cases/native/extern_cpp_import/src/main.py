from tpy.extern import native
from tpy import int32

@native("physics::calculate_force")
def calc_force(mass: float, accel: float) -> float: ...

@native
def global_func(x: int32) -> int32: ...
