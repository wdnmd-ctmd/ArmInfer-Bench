#!/usr/bin/env bash
# scripts/build_variant.sh
#
# T2: 按档位参数化构建 llama.cpp(五档之一)。严格遵守 NF4 同机对照:五档在同一
# runner 串行构建,ccache 跨档共享 llama/ggml 核心目标控时(G3)。
#
# Build-time 激活探针(G1):本脚本只采集 build-time 证据——
#   - kleidiai_compiled:nm 符号计数(kai_,definitive,条件编译)
#   - cmake option 状态(GGML_CPU_REPACK / GGML_CPU_KLEIDIAI,交叉验证 nm)
#   - arch flag(-march=armv9-a+dotprod+i8mm+sve2 是否落地)
#   - cmake STATUS 行(KleidiAI / HAVE_DOTPROD 等)
# 运行时探针(repack_active / kleidiai_active / tensors_offloaded)由 bench.yml 用
# `llama-bench -v` 日志采集,本脚本不负责。
#
# 用法:bash scripts/build_variant.sh <variant> <src_dir> <build_dir>
#   variant ∈ {naive, norepack, repack, kleidiai_only, kleidiai}

set -euo pipefail

VARIANT="${1:-}"
SRC_DIR="${2:-}"
BUILD_DIR="${3:-}"

if [[ -z "$VARIANT" || -z "$SRC_DIR" || -z "$BUILD_DIR" ]]; then
    echo "usage: $0 <variant> <src_dir> <build_dir>" >&2
    echo "  variant ∈ {naive, norepack, repack, kleidiai_only, kleidiai}" >&2
    exit 2
fi

case "$VARIANT" in
    naive)
        ARCH="armv8-a"
        KLEIDIAI="OFF"
        REPACK="OFF"
        ;;
    norepack)
        ARCH="armv9-a+dotprod+i8mm+sve2"
        KLEIDIAI="OFF"
        REPACK="OFF"
        ;;
    repack)
        ARCH="armv9-a+dotprod+i8mm+sve2"
        KLEIDIAI="OFF"
        REPACK="ON"
        ;;
    kleidiai_only)
        ARCH="armv9-a+dotprod+i8mm+sve2"
        KLEIDIAI="ON"
        REPACK="OFF"
        ;;
    kleidiai)
        ARCH="armv9-a+dotprod+i8mm+sve2"
        KLEIDIAI="ON"
        REPACK="ON"
        ;;
    *)
        echo "::error::unknown variant '$VARIANT'; must be one of naive/norepack/repack/kleidiai_only/kleidiai" >&2
        exit 2
        ;;
esac

echo "::group::build_variant.sh: $VARIANT"
echo "variant   : $VARIANT"
echo "arch      : $ARCH"
echo "kleidiai  : $KLEIDIAI"
echo "repack    : $REPACK"
echo "src_dir   : $SRC_DIR"
echo "build_dir : $BUILD_DIR"

# --- Configure (capture full cmake config log for activation probe evidence) ---
# GGML_LOG_LEVEL=DEBUG so runtime -v logs surface repack/kleidiai DEBUG messages (G1).
# (Option may not exist on all commits; pass through, cmake ignores unknown -D only if
#  marked as such — guard with a feature probe is overkill; we set env instead at bench.)
cmake -S "$SRC_DIR" -B "$BUILD_DIR" \
    -DGGML_NATIVE=OFF \
    -DGGML_CPU_ARM_ARCH="$ARCH" \
    -DGGML_CPU_KLEIDIAI="$KLEIDIAI" \
    -DGGML_CPU_REPACK="$REPACK" \
    -DGGML_CCACHE=ON \
    -DCMAKE_BUILD_TYPE=Release \
    -DLLAMA_BUILD_TESTS=OFF \
    -DLLAMA_BUILD_EXAMPLES=OFF \
    -DLLAMA_BUILD_SERVER=OFF \
    2>&1 | tee "$BUILD_DIR/cmake_config.log"

# --- Build only llama-bench target (control CI time, G3) ---
cmake --build "$BUILD_DIR" --target llama-bench -j"$(nproc)"

BENCH_BIN="$BUILD_DIR/bin/llama-bench"
if [[ ! -x "$BENCH_BIN" ]]; then
    echo "::error::llama-bench not found at $BENCH_BIN" >&2
    exit 1
fi

echo "=== llama-bench binary ==="
"$BENCH_BIN" --version || true

# ============================================================================
# Build-time activation probes (G1). Runtime probes captured per-bench in bench.yml.
# ============================================================================
PROBE_FILE="$BUILD_DIR/probe_build.txt"
: > "$PROBE_FILE"

# Probe 1: kleidiai_compiled — nm symbol count (definitive, conditional compilation).
# b9728 默认静态链接(BUILD_SHARED_LIBS=OFF),kai_ 符号在 llama-bench 内;
# 若动态链接则落在 libggml*.so,fallback 扫 build 目录下的 ggml shared libs。
NM_COUNT=0
if command -v nm >/dev/null 2>&1; then
    NM_COUNT=$(nm -a "$BENCH_BIN" 2>/dev/null | grep -cE ' [TtWw] kai_' || true)
    if [[ "$NM_COUNT" -eq 0 && "$KLEIDIAI" == "ON" ]]; then
        # Fallback: scan ggml shared libs (KleidiAI symbols may live in libggml-cpu.so).
        LIBS=$(find "$BUILD_DIR" -name 'libggml*.so*' 2>/dev/null | head -20)
        if [[ -n "$LIBS" ]]; then
            NM_COUNT=$(nm -a $LIBS 2>/dev/null | grep -cE ' [TtWw] kai_' || true)
        fi
    fi
fi
echo "kleidiai_compiled_nm_count=$NM_COUNT" | tee -a "$PROBE_FILE"

# Probe 2/3: cmake option state (cross-validation, build-time, from CMakeCache).
REPACK_CMAKE=$(grep -E "^GGML_CPU_REPACK:BOOL=" "$BUILD_DIR/CMakeCache.txt" 2>/dev/null | cut -d= -f2 || echo "UNKNOWN")
KLEIDIAI_CMAKE=$(grep -E "^GGML_CPU_KLEIDIAI:BOOL=" "$BUILD_DIR/CMakeCache.txt" 2>/dev/null | cut -d= -f2 || echo "UNKNOWN")
echo "repack_cmake_state=$REPACK_CMAKE"     | tee -a "$PROBE_FILE"
echo "kleidiai_cmake_state=$KLEIDIAI_CMAKE" | tee -a "$PROBE_FILE"

# Probe 4: arch flag actually used (cross-validation +dotprod+i8mm+sve2 landed).
ARCH_FLAG=$(grep -oE '\-march=[^ "]+' "$BUILD_DIR/CMakeCache.txt" 2>/dev/null | head -1 || echo "")
echo "arch_flag=$ARCH_FLAG" | tee -a "$PROBE_FILE"

# Surface cmake STATUS evidence (KleidiAI / repack / HAVE_* / march) — G1 real evidence.
echo "=== cmake STATUS evidence (KleidiAI / repack / arch / HAVE_*) ===" | tee -a "$PROBE_FILE"
grep -iE 'kleidiai|repack|march=armv9|HAVE_(DOTPROD|MATMUL_INT8|SVE|i8mm)|-march=armv' "$BUILD_DIR/cmake_config.log" 2>/dev/null | tee -a "$PROBE_FILE" || echo "(no matching STATUS lines)" | tee -a "$PROBE_FILE"

echo "::endgroup::"
echo "build_variant.sh: $VARIANT done. probe file: $PROBE_FILE"
