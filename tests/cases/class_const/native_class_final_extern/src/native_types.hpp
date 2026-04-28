#pragma once
#include <cstdint>
#include <string_view>

struct BuildOpts {
    static constexpr bool FLAG = true;
    static constexpr int32_t MAX_RETRIES = 7;
    static constexpr std::string_view RELEASE_TAG = "v1.0";
};
