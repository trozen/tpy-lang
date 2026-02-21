# Marker protocol (no methods) cannot be @dynamic
from tpy import dynamic
from typing import Protocol

@dynamic
class Marker(Protocol):  # tpyc: error(/@dynamic protocol 'Marker' must have at least one method/)
    ...
