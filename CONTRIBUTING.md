
## CONTRIBUTING

### Build the Android app

The Android project is in `Android/`. From the repository root, build it with:

```bash
./Android/gradlew -p Android assembleDebug
```

The APK is output to:

```
Android/app/build/outputs/apk/debug/app-debug.apk
```

### Connect via USB with adb reverse

For development or when Wi-Fi is unavailable, you can forward the relay to the phone over USB:

```bash
adb reverse tcp:8765 tcp:8765
```

Then set the app URL to:

```
http://127.0.0.1:8765
```

This is mainly useful for testing on a physical device or emulator.

### Validation

The Python relay uses only the standard library and has no external dependencies. Run the test suite with:

```bash
python server/test_mpvremote.py
```

The USB development workflow requires `adb` from the Arch Linux
`android-tools` package.

