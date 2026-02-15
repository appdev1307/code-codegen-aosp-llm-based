#!/bin/bash

echo "=== Testing Generated Code ==="

OUTPUT_DIR="/content/code-codegen-aosp-llm-based/output"

# Test 1: Check if files exist
echo "[TEST 1] File existence check..."
test -f "$OUTPUT_DIR/hardware/interfaces/automotive/vehicle/aidl/android/hardware/automotive/vehicle/IVehicle.aidl" && echo "✓ AIDL interface exists" || echo "✗ AIDL missing"

# Test 2: Count generated files
echo "[TEST 2] File count..."
find "$OUTPUT_DIR" -name "*.java" -o -name "*.kt" -o -name "*.cpp" -o -name "*.py" | wc -l

# Test 3: Check for compilation markers
echo "[TEST 3] Build file check..."
test -f "$OUTPUT_DIR/hardware/interfaces/automotive/vehicle/Android.bp" && echo "✓ Build file exists" || echo "✗ Build file missing"

# Test 4: Python backend syntax check
echo "[TEST 4] Python syntax validation..."
find "$OUTPUT_DIR/backend" -name "*.py" -exec python3 -m py_compile {} \; 2>&1 | grep -q "SyntaxError" && echo "✗ Syntax errors found" || echo "✓ Python syntax valid"

# Test 5: Check for TODOs/placeholders
echo "[TEST 5] Placeholder check..."
grep -r "TODO\|FIXME\|PLACEHOLDER" "$OUTPUT_DIR" | wc -l | xargs echo "TODOs found:"

