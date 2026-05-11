# Declares the @native binding for lib::Color. Other modules import Color from here.
# tpy: include("native_types.hpp")
from enum import Enum, auto
from tpy.extern import native


@native("lib::Color")
class Color(Enum):
    RED = auto()
    GREEN = auto()
    BLUE = auto()
