# Tests answered False for two unrelated classes, analyzed before the module
# that defines a class deriving both (main.py's `Ray`): the warning still
# names it.
from tpy import int32


class Fin:
    fins: int32

    def __init__(self) -> None:
        self.fins = 2


class Wing:
    wings: int32

    def __init__(self) -> None:
        self.wings = 4


def mi_isinstance(a: Fin) -> str:
    if isinstance(a, Wing):  # tpyc: warning(/isinstance\(\) check against 'Wing' on 'Fin' folds to False .*'Ray' derives both/)
        return "wing"
    return "fin"


def mi_pattern(a: Fin) -> str:
    match a:
        case Wing():  # tpyc: warning(/class pattern 'Wing' on 'Fin' never matches .*'Ray' derives both/)
            return "wing"
        case _:
            return "fin"


def mi_union(v: Fin | int32) -> str:
    if isinstance(v, Wing):  # tpyc: warning(/isinstance\(\) check against 'Wing' on 'Fin' folds to False .*'Ray' derives both/)
        return "wing"
    return "other"
