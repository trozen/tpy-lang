# tpy: ext_module
# @property on an exposed class crosses as a computed getset: the getter is a
# zero-arg method return site (full method boundary set, incl. containers),
# the setter a one-param method param site. A getter-only property is
# read-only and `del` raises AttributeError on any property (no deleters); a
# raise inside either accessor crosses like a method raise. `_`-named
# properties are internal (never a Python attribute; ext-only, ext_checks.py).
from enum import IntEnum
from tpy import int32, Own
from tpy.extern import export


@export
class Color(IntEnum):
    RED = 1
    GREEN = 2
    BLUE = 3


@export
class Rect:
    name: str
    _w: int32
    _h: int32
    _color: Color
    _tags: list[int32]

    def __init__(self, w: int32, h: int32) -> None:
        self.name = "rect"
        self._w = w
        self._h = h
        self._color = Color.RED
        self._tags = []

    @property
    def area(self) -> int32:
        return self._w * self._h

    @property
    def width(self) -> int32:
        return self._w

    @width.setter
    def width(self, v: int32) -> None:
        if v < 0:
            raise ValueError("negative width")
        self._w = v

    @property
    def label(self) -> str:
        if self._w > 100:
            raise ValueError("too wide to label")
        return "rect"

    @property
    def dims(self) -> Own[list[int32]]:
        return [self._w, self._h]

    @property
    def color(self) -> Color:
        return self._color

    @color.setter
    def color(self, c: Color) -> None:
        self._color = c

    @property
    def tags(self) -> Own[list[int32]]:
        out: list[int32] = []
        for t in self._tags:
            out.append(t)
        return out

    @tags.setter
    def tags(self, v: list[int32]) -> None:
        self._tags = v

    @property
    def blob(self) -> bytes:
        return b"pb"

    @property
    def mapping(self) -> Own[dict[str, int32]]:
        return {"w": self._w, "h": self._h}

    @property
    def _diag(self) -> int32:
        return self._w + self._h

    @property
    def _scale(self) -> int32:
        return self._w

    @_scale.setter
    def _scale(self, v: int32) -> None:
        self._w = v
