#!/bin/bash
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo 'Run this script on macOS.' >&2
  exit 1
fi

: "${FLICK_APPLE_DISTRIBUTION_IDENTITY:?Set the Apple Distribution identity installed in Keychain}"
: "${FLICK_MAC_INSTALLER_IDENTITY:?Set the Mac Installer Distribution identity installed in Keychain}"

cd "$(dirname "$0")/.."
mkdir -p build/Flick.iconset dist
for size in 16 32 128 256 512; do
  sips -z "$size" "$size" assets/flick-icon-v2.png \
    --out "build/Flick.iconset/icon_${size}x${size}.png" >/dev/null
  double=$((size * 2))
  sips -z "$double" "$double" assets/flick-icon-v2.png \
    --out "build/Flick.iconset/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns build/Flick.iconset -o assets/flick.icns

python -m PyInstaller --noconfirm Flick.store.spec

if [[ -n "${FLICK_STORE_PROFILE:-}" ]]; then
  cp "$FLICK_STORE_PROFILE" dist/Flick.app/Contents/embedded.provisionprofile
fi

# Downloaded provisioning profiles may carry quarantine metadata, which App Store
# Connect rejects when it is embedded in the signed app.
xattr -cr dist/Flick.app

codesign --force --sign "$FLICK_APPLE_DISTRIBUTION_IDENTITY" \
  --entitlements packaging/flick-app-store.entitlements \
  --options runtime --timestamp dist/Flick.app
codesign --verify --deep --strict --verbose=2 dist/Flick.app
codesign --display --entitlements - --xml dist/Flick.app \
  | plutil -convert xml1 -o - - > build/store-entitlements.plist
python -c "import plistlib; p=plistlib.load(open('build/store-entitlements.plist','rb')); assert p['com.apple.security.app-sandbox']; assert p['com.apple.security.files.user-selected.read-only']"

package=dist/Flick-macOS-arm64-store.pkg
productbuild --sign "$FLICK_MAC_INSTALLER_IDENTITY" \
  --component dist/Flick.app /Applications "$package"
pkgutil --check-signature "$package"
echo "Ready for local installation test: $package"
