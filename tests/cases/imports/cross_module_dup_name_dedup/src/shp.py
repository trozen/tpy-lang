# Single record imported twice (under two names) by main -- the union of the
# two names must dedup back to one member.
class Point:
    def __init__(self, x: int):
        self.x = x
