# Defines a record type for cross-module import
from tpy import Int32


class Circle:
    radius: Int32

    def __init__(self, radius: Int32) -> None:
        self.radius = radius
