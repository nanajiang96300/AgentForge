#!/bin/bash
# Pre-fix state: Bug #1 (no-args crash) and Bug #2 (div-by-zero silent inf) EXIST
# Post-fix state: Both bugs fixed, all 4 tests PASS

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_DIR="$PROJECT_DIR/build"
PASSED=0
FAILED=0

echo "=== Calculator Tests ==="
echo ""

# Test 1: normal addition
echo -n "[1/4] add 5 3: "
if OUTPUT=$("$BUILD_DIR/calc" add 5 3 2>&1); then
    if echo "$OUTPUT" | grep -q "8"; then
        echo "PASS (Result: 8)"
        PASSED=$((PASSED + 1))
    else
        echo "FAIL (unexpected: $OUTPUT)"
        FAILED=$((FAILED + 1))
    fi
else
    echo "FAIL (crashed with exit $?)"
    FAILED=$((FAILED + 1))
fi

# Test 2: normal division
echo -n "[2/4] div 10 2: "
if OUTPUT=$("$BUILD_DIR/calc" div 10 2 2>&1); then
    if echo "$OUTPUT" | grep -q "5"; then
        echo "PASS (Result: 5)"
        PASSED=$((PASSED + 1))
    else
        echo "FAIL (unexpected: $OUTPUT)"
        FAILED=$((FAILED + 1))
    fi
else
    echo "FAIL (crashed with exit $?)"
    FAILED=$((FAILED + 1))
fi

# Test 3: division by zero — Bug #2
echo -n "[3/4] div 10 0 (Bug #2): "
OUTPUT=$("$BUILD_DIR/calc" div 10 0 2>&1) || EXIT_CODE=$?
if echo "$OUTPUT" | grep -qiE "inf|nan|infinity"; then
    echo "FAIL (Bug #2 present: returns $OUTPUT — silent inf/nan)"
    FAILED=$((FAILED + 1))
elif [ ${EXIT_CODE:-0} -ne 0 ]; then
    # Non-zero exit = error handled (post-fix behavior)
    echo "PASS (error handled: exit $EXIT_CODE)"
    PASSED=$((PASSED + 1))
else
    echo "PASS (no inf/nan, clean exit)"
    PASSED=$((PASSED + 1))
fi

# Test 4: no arguments — Bug #1
echo -n "[4/4] no args (Bug #1): "
OUTPUT=$("$BUILD_DIR/calc" 2>&1) || EXIT_CODE=$?
if [ ${EXIT_CODE:-0} -ge 128 ]; then
    echo "FAIL (Bug #1 present: crash signal $EXIT_CODE)"
    FAILED=$((FAILED + 1))
elif [ ${EXIT_CODE:-0} -ne 0 ]; then
    echo "PASS (graceful error: exit $EXIT_CODE)"
    PASSED=$((PASSED + 1))
else
    echo "PASS (clean exit)"
    PASSED=$((PASSED + 1))
fi

echo ""
echo "=== Pre-fix expectation: 2/4 pass (Bug #1 + Bug #2 fail) ==="
echo "=== Results: $PASSED passed, $FAILED failed ==="
exit $FAILED
