"""Build a local menu-bar companion. This does not bundle Python or customer secrets."""

import argparse
import json
import plistlib
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("dist/VirtualYou.app"))
    args = parser.parse_args()
    if sys.platform != "darwin":
        raise SystemExit("Build this companion on macOS.")
    root = Path(__file__).resolve().parents[1]
    app = args.output.resolve()
    if app.exists():
        raise SystemExit("Choose a new output path; an existing bundle will not be overwritten.")
    macos = app / "Contents/MacOS"
    resources = app / "Contents/Resources"
    macos.mkdir(parents=True)
    resources.mkdir()
    subprocess.run(
        [
            "xcrun",
            "swiftc",
            str(root / "desktop/VirtualYou.swift"),
            "-framework",
            "AppKit",
            "-o",
            str(macos / "VirtualYou"),
        ],
        check=True,
    )
    with (app / "Contents/Info.plist").open("wb") as stream:
        plistlib.dump(
            {
                "CFBundleExecutable": "VirtualYou",
                "CFBundleIdentifier": "local.virtualyou.companion",
                "CFBundleName": "VirtualYou",
                "CFBundlePackageType": "APPL",
                "CFBundleVersion": "1",
                "CFBundleShortVersionString": "0.2",
                "LSUIElement": True,
                "NSHighResolutionCapable": True,
                "LSMinimumSystemVersion": "12.0",
            },
            stream,
        )
    (resources / "runtime.json").write_text(
        json.dumps({"python": str(Path(sys.executable).absolute()), "root": str(root)})
    )
    subprocess.run(["xattr", "-cr", str(app)], check=True)
    subprocess.run(["codesign", "--force", "--sign", "-", str(app)], check=True)
    subprocess.run(["codesign", "--verify", "--strict", str(app)], check=True)
    print(
        f"Built local development companion: {app}. Keep this checkout and virtual environment in place."
    )


if __name__ == "__main__":
    main()
