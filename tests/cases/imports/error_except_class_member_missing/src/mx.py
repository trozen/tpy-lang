# A class with a nested exception, but no `Missing`.
class Outer:
    class Err(Exception):
        pass
