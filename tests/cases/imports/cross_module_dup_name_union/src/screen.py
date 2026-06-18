# A screen-space point. Same class name as world.Point but a different field,
# so any short-name collapse of the two surfaces as a missing-field error.
class Point:
    def __init__(self, x: int):
        self.x = x
