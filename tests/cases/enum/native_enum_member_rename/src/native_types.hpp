// C++ enum with members whose names clash with Python keywords (`None`,
// `True`, `False`) or use different conventions. The TPy binding uses
// native_member("...") to map TPy-side names to the C++ symbols.
#pragma once

namespace cfg {

enum class Mode : int {
    None = 0,
    Auto = 1,
    Manual = 2,
};

}  // namespace cfg
