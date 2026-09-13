"""Upload the Moltbook credential to GitHub Actions without a command-line leak."""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def read_key(path: Path) -> str:
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.startswith("MOLTBOOK_API_KEY="):
            value = line.partition("=")[2].strip().strip('"').strip("'")
            if value:
                return value
    raise RuntimeError("MOLTBOOK_API_KEY is absent")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--repo", default="mike-lblc/project-zero")
    args = parser.parse_args()
    key = read_key(args.env_file)
    result = subprocess.run(
        ["gh", "secret", "set", "P0_MOLTBOOK_API_KEY", "--repo", args.repo],
        input=key,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    if result.returncode:
        print("GitHub secret update failed: " + result.stderr.strip()[:300])
        return result.returncode
    check = subprocess.run(
        ["gh", "secret", "list", "--repo", args.repo, "--json", "name"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    exists = '"P0_MOLTBOOK_API_KEY"' in check.stdout
    print({"repo": args.repo, "secret": "P0_MOLTBOOK_API_KEY", "exists": exists})
    return 0 if check.returncode == 0 and exists else 1


if __name__ == "__main__":
    raise SystemExit(main())
