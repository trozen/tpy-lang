"""Module exporting a Final[str] constant -- consumer imports it to verify that
macro_api._is_static_str recognises Final[str] references across modules."""
from typing import Final

VERSION: Final[str] = "1.2.3"
