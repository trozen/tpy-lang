# A cycle member's operator may not return a tuple holding a peer's value
# type: the in-class operator returns it by value (docs/LANGUAGE_FEATURES.md "Circular imports").
import a

print(a.run())
