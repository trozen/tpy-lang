# Native C struct declarations for cross-module import
from tpy import native_c, Int32, Ptr

@native_c("thing_t")
class ThingT:
    id: Int32

@native_c("sector_t")
class SectorT:
    floor_height: Int32
    thinglist: Ptr[None]
