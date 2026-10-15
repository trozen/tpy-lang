# A cycle member's operator may not return a peer's value type: its C++
# operator is defined in the class, in the header (docs/LANGUAGE_FEATURES.md "Circular imports").
import a

print(a.run())
