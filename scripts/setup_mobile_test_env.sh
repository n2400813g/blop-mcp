#!/usr/bin/env bash
# setup_mobile_test_env.sh
# Sets up the full blop mobile testing environment and runs integration tests.
#
# Usage:
#   ./scripts/setup_mobile_test_env.sh [ios|android|both]
#
# Requires:
#   macOS with Xcode installed (for iOS)
#   Android Studio + SDK + emulator (for Android)
#   Node.js 18+ and npm

set -euo pipefail

PLATFORM="${1:-both}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
APPS_DIR="$REPO_ROOT/tests/apps"

echo "=== blop Mobile Test Environment Setup ==="
echo "Platform: $PLATFORM"
echo ""

# ── 1. Appium server ──────────────────────────────────────────────────────────

echo "--- Installing Appium 3..."
npm install -g appium 2>/dev/null || npx appium --version

echo "--- Installing Appium drivers..."
if [[ "$PLATFORM" == "ios" || "$PLATFORM" == "both" ]]; then
  npx appium driver install xcuitest --no-color 2>/dev/null || true
fi
if [[ "$PLATFORM" == "android" || "$PLATFORM" == "both" ]]; then
  npx appium driver install uiautomator2 --no-color 2>/dev/null || true
fi

echo "--- Installed drivers:"
npx appium driver list --no-color 2>/dev/null || true

# ── 2. Python client ──────────────────────────────────────────────────────────

echo ""
echo "--- Installing blop-mcp[mobile] Python extras..."
cd "$REPO_ROOT"
uv pip install -e ".[mobile]" 2>/dev/null || pip install -e ".[mobile]"

# ── 3. Download test apps ─────────────────────────────────────────────────────

echo ""
echo "--- Checking test app binaries in $APPS_DIR ..."
mkdir -p "$APPS_DIR"

if [[ "$PLATFORM" == "android" || "$PLATFORM" == "both" ]]; then
  if [[ ! -f "$APPS_DIR/mda-android.apk" ]] || [[ "$(stat -f%z "$APPS_DIR/mda-android.apk" 2>/dev/null || stat -c%s "$APPS_DIR/mda-android.apk" 2>/dev/null)" -lt 1000000 ]]; then
    echo "  Downloading Sauce Labs My Demo App Android APK..."
    curl -fsSL "https://github.com/saucelabs/my-demo-app-android/releases/download/2.2.0/mda-2.2.0-25.apk" \
      -o "$APPS_DIR/mda-android.apk"
    echo "  Downloaded: $(ls -lh "$APPS_DIR/mda-android.apk" | awk '{print $5}')"
  else
    echo "  mda-android.apk already present."
  fi
fi

if [[ "$PLATFORM" == "ios" || "$PLATFORM" == "both" ]]; then
  if [[ ! -f "$APPS_DIR/DVIA-v2.ipa" ]] || [[ "$(stat -f%z "$APPS_DIR/DVIA-v2.ipa" 2>/dev/null || stat -c%s "$APPS_DIR/DVIA-v2.ipa" 2>/dev/null)" -lt 1000000 ]]; then
    echo "  Downloading DVIA-v2 iOS IPA..."
    curl -fsSL "https://github.com/prateek147/DVIA-v2/releases/download/v2.0/DVIA-v2-swift.ipa" \
      -o "$APPS_DIR/DVIA-v2.ipa"
    echo "  Downloaded: $(ls -lh "$APPS_DIR/DVIA-v2.ipa" | awk '{print $5}')"
  else
    echo "  DVIA-v2.ipa already present."
  fi
fi

# ── 4. iOS simulator setup ────────────────────────────────────────────────────

if [[ "$PLATFORM" == "ios" || "$PLATFORM" == "both" ]]; then
  echo ""
  echo "--- iOS Simulator setup..."
  if ! command -v xcrun &>/dev/null || ! xcrun simctl list &>/dev/null; then
    echo "  WARNING: Xcode not installed or simctl unavailable."
    echo "  Install Xcode from the App Store, then re-run this script."
  else
    DEVICE="${BLOP_IOS_DEVICE:-iPhone 15}"

    # Boot simulator if not running
    UDID=$(xcrun simctl list devices available 2>/dev/null | grep "$DEVICE" | grep -oE '[A-F0-9-]{36}' | head -1)
    if [[ -n "$UDID" ]]; then
      STATE=$(xcrun simctl list devices 2>/dev/null | grep "$UDID" | grep -oE '\(.*\)' | tr -d '()')
      if [[ "$STATE" != "Booted" ]]; then
        echo "  Booting $DEVICE ($UDID)..."
        xcrun simctl boot "$UDID"
        sleep 10
      else
        echo "  $DEVICE already booted."
      fi
      # Install DVIA-v2
      echo "  Installing DVIA-v2.ipa on simulator..."
      xcrun simctl install booted "$APPS_DIR/DVIA-v2.ipa" || echo "  WARN: IPA install failed (may need resigned IPA for simulator)"
    else
      echo "  WARNING: Simulator '$DEVICE' not found. Available devices:"
      xcrun simctl list devices available 2>/dev/null | grep -E "iPhone|iPad" | head -8
    fi
  fi
fi

# ── 5. Android emulator setup ─────────────────────────────────────────────────

if [[ "$PLATFORM" == "android" || "$PLATFORM" == "both" ]]; then
  echo ""
  echo "--- Android emulator setup..."
  if ! command -v adb &>/dev/null; then
    echo "  WARNING: adb not found. Install Android Studio and add SDK platform-tools to PATH:"
    echo "  export ANDROID_HOME=\$HOME/Library/Android/sdk"
    echo "  export PATH=\$PATH:\$ANDROID_HOME/platform-tools:\$ANDROID_HOME/emulator"
  else
    echo "  ADB found: $(adb --version | head -1)"
    # Check for connected devices
    DEVICES=$(adb devices 2>/dev/null | grep -v "^List" | grep -v "^$" | wc -l | tr -d ' ')
    if [[ "$DEVICES" -eq 0 ]]; then
      echo "  No Android devices connected. Start an emulator:"
      echo "  \$ANDROID_HOME/emulator/emulator -avd Pixel_7_API_33 &"
      echo "  adb wait-for-device"
    else
      echo "  Found $DEVICES device(s). Installing APK..."
      adb install -r "$APPS_DIR/mda-android.apk" && echo "  APK installed." || echo "  WARN: APK install failed"
    fi
  fi
fi

# ── 6. Start Appium server ────────────────────────────────────────────────────

echo ""
echo "--- Starting Appium server..."
APPIUM_LOG="${BLOP_APPIUM_LOG:-/tmp/appium-$$.log}"
if lsof -i :4723 &>/dev/null; then
  echo "  Appium already running on port 4723."
else
  echo "  Starting Appium on port 4723 (background). Log: $APPIUM_LOG"
  nohup npx appium --port 4723 --log "$APPIUM_LOG" &
  APPIUM_PID=$!
  trap 'kill "$APPIUM_PID" 2>/dev/null || true' EXIT
  sleep 3
  if lsof -i :4723 &>/dev/null; then
    echo "  Appium started (PID $APPIUM_PID)."
  else
    echo "  WARN: Appium may not have started. Check $APPIUM_LOG"
  fi
fi

# ── 7. Run tests ──────────────────────────────────────────────────────────────

echo ""
echo "=== Running blop mobile integration tests ==="
cd "$REPO_ROOT"

if [[ "$PLATFORM" == "ios" ]]; then
  python -m pytest tests/integration/test_mobile_dvia_ios.py -m mobile -v --tb=short
elif [[ "$PLATFORM" == "android" ]]; then
  python -m pytest tests/integration/test_mobile_sauce_android.py -m mobile -v --tb=short
else
  python -m pytest tests/integration/ -m mobile -v --tb=short
fi

echo ""
echo "=== Done ==="
