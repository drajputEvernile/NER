"""Download every local NER model into Member_Verification/Models/.

Each model is downloaded, checked for completeness, then loaded and asked to
read one test sentence. Anything that fails is reported with its full
traceback and the command exits non-zero, so a half-downloaded or unusable
checkpoint is never mistaken for a good one.

Usage (from repo root):
  .\\.venv\\Scripts\\python.exe Member_Verification\\Models\\model_downloader\\__main__.py
  .\\.venv\\Scripts\\python.exe Member_Verification\\Models\\model_downloader\\__main__.py --force
  .\\.venv\\Scripts\\python.exe Member_Verification\\Models\\model_downloader\\__main__.py --check
"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent

if __package__:
    from . import gliner_large_v2_1, gliner_low, gliner_medium_v2_1
else:
    sys.path.insert(0, str(HERE))
    import gliner_large_v2_1
    import gliner_low
    import gliner_medium_v2_1

DOWNLOADERS = (
    gliner_large_v2_1,
    gliner_medium_v2_1,
    gliner_low,
)


def download_all(*, force: bool = False, check_only: bool = False) -> list[str]:
    """Download (or just check) every model; return the ids that failed."""
    sys.path.insert(0, str(HERE.parent))
    from _common import verify_complete, verify_loads
    from catalog import model_dir

    failed: list[str] = []
    for module in DOWNLOADERS:
        spec = module.SPEC
        try:
            if check_only:
                dest = model_dir(spec)
                print(f"checking {spec['id']} at {dest}")
                verify_complete(spec, dest)
                verify_loads(spec, dest)
                print(f"ok {spec['id']}")
            else:
                module.download(force=force)
        except Exception:
            failed.append(spec["id"])
            print(f"\nFAILED {spec['id']}:", file=sys.stderr)
            traceback.print_exc()
            print("", file=sys.stderr)
    return failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download and verify the NER models")
    parser.add_argument("--force", action="store_true", help="re-download even if present")
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not download; only verify what is already on disk",
    )
    args = parser.parse_args(argv)

    failed = download_all(force=args.force, check_only=args.check)
    total = len(DOWNLOADERS)
    print(f"\n{total - len(failed)}/{total} models ready")
    if failed:
        print(f"failed: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
