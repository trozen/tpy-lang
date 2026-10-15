# A cycle member's operator may not take a peer's value type by value: its C++
# operator is defined in the class, in the header (docs/LANGUAGE_FEATURES.md "Circular imports").
import a

print(a.run())
