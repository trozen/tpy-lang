# Helper functions for cross-module mutation inference tests.
from tpy import Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def sum_points(items: list[Point]) -> Int32:
    """Reads only -- mutated_params = {}."""
    total: Int32 = 0
    for p in items:
        total += p.x
    return total

def add_point(items: list[Point], p: Point) -> None:
    """Mutates via append -- mutated_params = {0}."""
    items.append(p)

def add_point_wrapper(items: list[Point], p: Point) -> None:
    """Transitively mutates via add_point -- mutated_params = {0}."""
    add_point(items, p)

def read_wrapper(items: list[Point]) -> Int32:
    """Transitively reads via sum_points -- mutated_params = {}."""
    return sum_points(items)
