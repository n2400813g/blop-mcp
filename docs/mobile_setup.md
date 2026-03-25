# Mobile App Testing — Local Setup

This guide covers everything needed to run blop mobile tests locally against iOS simulators and Android emulators. It is intended for operators setting up a new machine and for early users encountering setup issues.

blop uses **Appium 3** as its mobile execution driver, with **XCUITest** for iOS and **UiAutomator2** for Android.

---

## Prerequisites

### All platforms

| Requirement | Minimum version | Check |
|---|---|---|
| Node.js | 18 | `node --version` |
| npm | 9 | `npm --version` |
| Appium (global) | 3.x | `appium --version` |
| Python | 3.11+ | `python --version` |
| blop mobile extra | current | `pip show Appium-Python-Client` |

### iOS only (macOS required)

| Requirement | Notes |
|---|---|
| macOS | iOS simulator requires macOS |
| Xcode | Install from the App Store |
| Xcode Command Line Tools | `xcode-select --install` |
| iOS Simulator | Included with Xcode |

### Android only

| Requirement | Notes |
|---|---|
| Android Studio | Includes SDK Manager and emulator |
| Android SDK (API 33+) | Install via SDK Manager |
| `ANDROID_HOME` | `export ANDROID_HOME=$HOME/Library/Android/sdk` |
| Platform Tools on `PATH` | `export PATH=$PATH:$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator` |

---

## Installation

### 1. Install Appium 3 and drivers

```bash
npm install -g appium

# iOS
appium driver install xcuitest

# Android
appium driver install uiautomator2

# Verify
appium driver list
```

### 2. Install blop mobile Python extra

```bash
pip install blop-mcp[mobile]
# or in development
uv pip install -e ".[mobile]"
```

---

## Simulator / Emulator Setup

### iOS: Boot an iPhone simulator

```bash
# List available simulators
xcrun simctl list devices available

# Boot iPhone 15 (iOS 17)
xcrun simctl boot "iPhone 15"

# Or use the Simulator app
open -a Simulator
```

**Expected device name/version for tests:**

| Env var | Default |
|---|---|
| `BLOP_IOS_DEVICE` | `iPhone 15` |
| `BLOP_IOS_VERSION` | `17.0` |

### Android: Start an emulator

```bash
# List available AVDs
$ANDROID_HOME/emulator/emulator -list-avds

# Start Pixel 7 (API 33)
$ANDROID_HOME/emulator/emulator -avd Pixel_7_API_33 &

# Wait for it to boot
adb wait-for-device

# Verify device is listed
adb devices
```

**Expected device name/version for tests:**

| Env var | Default |
|---|---|
| `BLOP_ANDROID_DEVICE` | `Pixel 7` |
| `BLOP_ANDROID_VERSION` | `13.0` |

---

## App Artifact Provisioning

blop's integration tests use publicly available demo apps. Your production flows use your own app binary.

### Test apps (integration tests only)

The setup script downloads these automatically:

| Platform | App | Location |
|---|---|---|
| Android | Sauce Labs My Demo App (`mda-android.apk`) | `tests/apps/mda-android.apk` |
| iOS | DVIA-v2 (`DVIA-v2.ipa`) | `tests/apps/DVIA-v2.ipa` |

```bash
# Download and install test apps automatically
./scripts/setup_mobile_test_env.sh [ios|android|both]
```

### Your own app

For real flows against your app, provide one of:

- **`app_path`** — absolute path to your `.apk` / `.ipa` / `.app` file. blop will install it each session.
- **`app_id`** — bundle ID (iOS) or package name (Android) of an already-installed app. Required if `app_path` is not set.

```python
MobileDeviceTarget(
    platform="android",
    app_id="com.example.myapp",       # already installed
    # or
    app_path="/path/to/myapp.apk",    # blop installs on launch
    device_name="Pixel 7",
    os_version="13.0",
)
```

**IPA note:** `.ipa` files require re-signing for iOS simulators. Use `.app` bundles for simulator targets. Use `.ipa` for physical devices (requires provisioning).

---

## Starting the Appium Server

Appium must be running before blop mobile tools execute.

```bash
# Start on default port 4723
appium

# Or start in background and log to file
nohup appium --port 4723 --log /tmp/appium.log &

# Verify it is running
curl http://127.0.0.1:4723/status
```

The default server URL is `http://127.0.0.1:4723`. Override with:

```bash
export BLOP_APPIUM_URL=http://127.0.0.1:4723
```

---

## Validation

Run the blop doctor check to confirm your mobile setup is ready:

```python
validate_setup(check_mobile=True)
```

Or from the CLI:

```bash
python -c "
import asyncio
from blop.tools.validate import validate_setup
result = asyncio.run(validate_setup(check_mobile=True))
for c in result['checks']:
    status = '✓' if c['passed'] else '✗'
    print(f'{status} {c[\"name\"]}: {c[\"message\"]}')
"
```

---

## Common Setup Failures

### Appium not reachable

**Symptom:** `RuntimeError: Failed to create Appium session` or `validate_setup` reports `appium_server` failed.

**Fix:** Start the Appium server before running any mobile tool.

```bash
appium &
sleep 3
curl http://127.0.0.1:4723/status   # should return {"value":{"ready":true}}
```

### `pip install blop-mcp[mobile]` not installed

**Symptom:** `RuntimeError: Mobile testing requires the 'mobile' extra`.

**Fix:**

```bash
pip install blop-mcp[mobile]
```

### iOS simulator not found or not booted

**Symptom:** Appium session fails with `Could not find device` or `Simulator not booted`.

**Fix:**

```bash
xcrun simctl boot "iPhone 15"
# Wait until state shows Booted:
xcrun simctl list devices | grep "iPhone 15"
```

### Android emulator not found (`adb devices` empty)

**Symptom:** `adb devices` shows no devices.

**Fix:** Start your AVD and wait for it to boot:

```bash
$ANDROID_HOME/emulator/emulator -avd Pixel_7_API_33 &
adb wait-for-device
adb devices
```

If `adb` is not found, add Android SDK platform-tools to your PATH:

```bash
export ANDROID_HOME=$HOME/Library/Android/sdk
export PATH=$PATH:$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator
```

### IPA install fails on iOS simulator

**Symptom:** `xcrun simctl install booted DVIA-v2.ipa` fails.

**Fix:** Use the Xcode-built `.app` bundle for simulators. IPAs require a physical device or re-signing:

```bash
# Use the .app bundle from a simulator build instead
xcrun simctl install booted /path/to/MyApp.app
```

### Appium driver not installed

**Symptom:** `No driver found for 'XCUITest'` or `No driver found for 'UiAutomator2'`.

**Fix:**

```bash
appium driver install xcuitest        # iOS
appium driver install uiautomator2    # Android
appium driver list                    # verify
```

### Wrong Appium URL

**Symptom:** `urllib.error.URLError: [Errno 61] Connection refused`.

**Fix:** Verify Appium is running on the expected port and that `BLOP_APPIUM_URL` matches:

```bash
lsof -i :4723              # confirm port in use
export BLOP_APPIUM_URL=http://127.0.0.1:4723
```

---

## Full Automated Setup

For a clean machine, the setup script handles all of the above:

```bash
./scripts/setup_mobile_test_env.sh both
```

This installs Appium, drivers, Python extras, downloads test app binaries, boots simulators/emulators, and starts Appium.

---

## Running Integration Tests

Once setup is complete:

```bash
# iOS
pytest tests/integration/test_mobile_dvia_ios.py -m mobile -v

# Android
pytest tests/integration/test_mobile_sauce_android.py -m mobile -v

# Both
pytest tests/integration/ -m mobile -v
```

Tests are marked `mobile`, `slow`, and `integration`. They require a running Appium server, a booted simulator/emulator, and the test app installed.

---

## Environment Variables Reference

| Variable | Default | Purpose |
|---|---|---|
| `BLOP_APPIUM_URL` | `http://127.0.0.1:4723` | Appium server URL |
| `BLOP_IOS_DEVICE` | `iPhone 15` | iOS simulator device name |
| `BLOP_IOS_VERSION` | `17.0` | iOS version |
| `BLOP_ANDROID_DEVICE` | `Pixel 7` | Android emulator device name |
| `BLOP_ANDROID_VERSION` | `13.0` | Android OS version |
| `ANDROID_HOME` | — | Android SDK root (required for Android) |

---

## Operational Notes

- **Appium must be started before blop mobile tools run.** There is no auto-start.
- **One Appium session per test.** Concurrent mobile tests use `max_concurrent=1` by default to avoid session contention.
- **Evidence artifacts** (screenshots, device logs) are stored under `runs/screenshots/<run_id>/` and `runs/console/<run_id>/`, same as web runs.
- **Mobile runs share the same SQLite DB** as web runs (`BLOP_DB_PATH`). No separate DB setup is needed.
- **Physical devices** are supported but require additional provisioning (signing, device trust). Simulators and emulators are the recommended path for local development.
