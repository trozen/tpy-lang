# Docstrings in class and method bodies are allowed (silently ignored).
from tpy import Int32

class Point:
    """A 2D point."""
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32):
        """Create a new Point."""
        self.x = x
        self.y = y

    def magnitude_sq(self) -> Int32:
        """Return the squared magnitude."""
        return self.x * self.x + self.y * self.y

def main() -> None:
    p = Point(3, 4)
    print(p.magnitude_sq())

main()
