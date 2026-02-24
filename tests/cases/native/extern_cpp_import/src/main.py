from tpy.extern import native
from tpy import Int32

@native("physics::calculate_force")
def calc_force(mass: float, accel: float) -> float: ...

@native
def global_func(x: Int32) -> Int32: ...
