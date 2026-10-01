# Flick for Mac App Store — submission preparation

This channel is separate from the GitHub ZIP and Windows installer. The App Store
build has bundle ID `media.kallos.flick`, currently targets Apple Silicon, and
must receive updates only through the Mac App Store.

## Account setup

1. In Apple Developer, register the explicit macOS App ID `media.kallos.flick`.
2. Check for **Apple Distribution** and **Mac Installer Distribution** signing
   certificates. A **Developer ID Application** certificate used for downloads
   outside the Store does not replace either one.
3. If the app later uses restricted capabilities or TestFlight, create a Mac App
   Store provisioning profile for this App ID. The current sandbox and
   user-selected-file entitlements do not themselves require one.
4. In App Store Connect, create the macOS app record: name `Flick`, bundle ID
   `media.kallos.flick`, primary language Korean or English, and an internal SKU.
   Confirm the public name is available before finalizing the record.

## Build and verify on a Mac

The GitHub `Flick macOS preview` workflow produces an **ad-hoc signed preview**
artifact named `Flick-macOS-arm64-store-preview`. It is for testing only. It is
not a signed installer package and cannot be uploaded to App Store Connect.

The Store build carries `flick-app-store.txt` and App Sandbox entitlements. Its
update button is absent. A user selects a **folder** and then a frame, so the
sandbox can read the adjacent files in the image sequence. Proxy files use the
macOS cache location, which resolves inside the app container when sandboxed.

Before submission, test the sandboxed app on a real Apple Silicon Mac with EXR,
JPG/PNG/TGA sequences, OCIO, Cryptomatte, proxy caching, and repeated launches.
Also verify behavior with sequences in Documents, external drives, and network
volumes. Check the Console for sandbox denials. OCIO configs referencing external
LUT files need particular attention.

For distribution, install both signing certificates in the Mac Keychain and run
`bash packaging/build_store_pkg.sh` on the Mac. Set the environment variables
`FLICK_APPLE_DISTRIBUTION_IDENTITY` and `FLICK_MAC_INSTALLER_IDENTITY` to the
identity names shown by `security find-identity -v`. If a provisioning profile
is required, set `FLICK_STORE_PROFILE` to its local path. The script signs the
complete app and its nested code, checks the sandbox entitlements, and creates
the installer package with `productbuild`. Keep certificates and private keys
out of the repository. Upload the tested package with Transporter or another
Apple-supported upload tool. The existing CI artifact is not that package.

## Store listing

- Product name: Flick
- Korean subtitle draft: `EXR·이미지 시퀀스 플레이어`
- English subtitle draft: `Fast image sequence viewer`
- Description: EXR, JPG, PNG and TGA image sequences; work-area preload;
  Full/Half/Quarter/Eighth playback; OCIO color display; Cryptomatte inspection.
- Prepare real Mac screenshots of the app and a public privacy-policy URL.
- Complete App Privacy, age rating, category, pricing/availability, support URL,
  and review contact in App Store Connect. Verify the privacy answers against
  the final binary and any third-party components.

Do not submit until both the signed package and sandboxed playback are verified.
