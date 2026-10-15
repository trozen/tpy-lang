# A cycle member's member template is defined in the header, so it may not take
# a `Span` of a peer's value type it iterates (docs/LANGUAGE_FEATURES.md "Circular imports").
import a

print(a.run())
