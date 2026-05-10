"""
Build script to package Storage Monitor as a standalone .exe.

Usage:
    python build.py          # builds .exe
    python build.py --clean  # clean build
"""

import subprocess
import sys
import os
import shutil
import argparse
import site

ROOT = os.path.dirname(os.path.abspath(__file__))


def run(cmd, cwd=None):
    print(f"\n>>> {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=cwd or ROOT)
    if result.returncode != 0:
        sys.exit(result.returncode)


def build_rust():
    """Build Rust extension via maturin (release mode, installs into current venv)."""
    print("\n=== Building Rust collector ===")
    run([sys.executable, "-m", "maturin", "develop",
         "--manifest-path", os.path.join("rust-collector", "Cargo.toml"),
         "--release"])


def find_pyd():
    """Locate the disk_collector .pyd file."""
    import disk_collector
    pyd_file = disk_collector.__file__
    print(f"Found pyd: {pyd_file}")
    return pyd_file


def build_exe():
    """Package into standalone .exe with PyInstaller."""
    print("\n=== Packaging .exe with PyInstaller ===")
    pyd_file = find_pyd()
    pyd_dir = os.path.dirname(pyd_file)
    pyd_name = os.path.basename(pyd_file)

    # PyInstaller args: bundle the .pyd as a binary so it ends up in the exe
    run([
        sys.executable, "-m", "PyInstaller",
        "--name", "StorageMonitor",
        "--windowed",
        "--add-binary", f"{pyd_file}{os.pathsep}.",
        "--add-data", f"gui{os.pathsep}gui",
        "--hidden-import", "disk_collector",
        "--noconfirm",
        "--clean",
        os.path.join("gui", "main.py"),
    ])

    print(f"\n=== Build complete ===")
    print(f"Output: {os.path.join(ROOT, 'dist', 'StorageMonitor')}")


def main():
    parser = argparse.ArgumentParser(description="Build Storage Monitor .exe")
    parser.add_argument("--clean", action="store_true",
                        help="Remove build artifacts first")
    args = parser.parse_args()

    if args.clean:
        for d in ["dist", "build", "rust-collector/target"]:
            path = os.path.join(ROOT, d)
            if os.path.exists(path):
                print(f"Removing {path}")
                shutil.rmtree(path)

    # Ensure build deps are present
    run([sys.executable, "-m", "pip", "install", "maturin", "PyInstaller", "PySide6"])

    build_rust()
    build_exe()


if __name__ == "__main__":
    main()
