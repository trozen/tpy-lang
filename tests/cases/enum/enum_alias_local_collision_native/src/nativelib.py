# Declares the @native binding for lib::Color.
__tpy__ = {"includes": ["native_types.hpp"]}
from enum import Enum, auto
from tpy.extern import native


@native("lib::Color")
class Color(Enum):
    RED = auto()
    GREEN = auto()
