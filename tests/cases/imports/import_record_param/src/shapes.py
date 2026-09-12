# Defines a record type for cross-module import
from tpy import int32


class Circle:
    radius: int32

    def __init__(self, radius: int32) -> None:
        self.radius = radius
