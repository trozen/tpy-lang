#pragma once
#include <cstdint>
#include <string_view>

struct BuildOpts {
    static constexpr bool g_flag = true;
    static constexpr int32_t kMaxRetries = 5;
    static constexpr std::string_view RELEASE_TAG = "v2.0";
};
