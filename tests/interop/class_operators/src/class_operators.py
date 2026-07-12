# tpy: ext_module
# Exposed-class dunders: arithmetic/ordering operators. __add__ (class +
# class) and __mul__/__rmul__ (class * scalar, both directions -- the common
# vector-scalar shape) -> Py_nb_add/Py_nb_multiply; __sub__/__rsub__ are
# ASYMMETRIC (unlike __mul__/__rmul__'s commutative scalar multiply) so they
# catch an operand-order bug the commutative pair can't: `a - b` must use
# (a.x - b.x), and `scalar - a` must use (scalar - a.x), not the reverse;
# __neg__ -> Py_nb_negative; __iadd__ -> Py_nb_inplace_add (mutates in place,
# returns the SAME object -- unlike the other dunders' fresh-instance
# returns); __pow__ -> Py_nb_power (ternary slot; a real modulus is rejected).
from __future__ import annotations
from tpy import Int64, Own
from tpy.extern import export


@export
class Vec2:
    def __init__(self, x: Int64, y: Int64):
        self.x = x
        self.y = y

    def __add__(self, other: Vec2) -> Own[Vec2]:
        return Vec2(self.x + other.x, self.y + other.y)

    def __sub__(self, other: Vec2) -> Own[Vec2]:
        return Vec2(self.x - other.x, self.y - other.y)

    def __rsub__(self, scalar: Int64) -> Own[Vec2]:
        return Vec2(scalar - self.x, scalar - self.y)

    def __mul__(self, scalar: Int64) -> Own[Vec2]:
        return Vec2(self.x * scalar, self.y * scalar)

    def __rmul__(self, scalar: Int64) -> Own[Vec2]:
        return Vec2(self.x * scalar, self.y * scalar)

    def __neg__(self) -> Own[Vec2]:
        return Vec2(-self.x, -self.y)

    def __iadd__(self, other: Vec2) -> Vec2:
        self.x += other.x
        self.y += other.y
        return self

    def __pow__(self, exponent: Int64) -> Own[Vec2]:
        return Vec2(self.x * exponent, self.y * exponent)
