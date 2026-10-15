# A cycle member's operator may not return `Own[...]` of a union of peer
# classes: it holds each class by value (docs/LANGUAGE_FEATURES.md "Circular imports").
import a

print(a.run())
