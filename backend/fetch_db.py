"""
Download rule1.db at deploy time.

rule1.db is not in git; on a host like Render it is pulled from a private
Hugging Face dataset during the build step:

    python backend/fetch_db.py

Environment variables:
    HF_DATASET_REPO  the dataset, as "owner/name"
    HF_TOKEN         a Hugging Face access token that can read it (secret)
    DB_TARGET        optional; where to write the file (default: rule1.db
                     next to this script). Handy for testing without touching
                     the real database.

If the target file already exists this does nothing. Otherwise it streams the
download to a temp file, checks it looks like a real SQLite database, and
only then moves it into place. Any failure prints a message and exits
nonzero. Standard library only; the token is never printed.
"""
from __future__ import annotations

import os
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_TARGET = Path(__file__).resolve().parent / "rule1.db"
URL_TEMPLATE = "https://huggingface.co/datasets/{repo}/resolve/main/rule1.db"
MIN_BYTES = 1024 * 1024
SQLITE_MAGIC = b"SQLite format 3"
CHUNK_BYTES = 1024 * 1024
TIMEOUT_SECONDS = 60


class _DropAuthOnCrossHostRedirect(urllib.request.HTTPRedirectHandler):
    """Hugging Face redirects /resolve/ to a pre-signed CDN URL. urllib would
    resend the Authorization header there; drop it so the token only ever
    goes to the host it was meant for."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlsplit(newurl).netloc != urllib.parse.urlsplit(req.full_url).netloc:
            new.headers.pop("Authorization", None)
            new.unredirected_hdrs.pop("Authorization", None)
        return new


def fail(message: str) -> None:
    print(f"fetch_db: ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    target = Path(os.environ.get("DB_TARGET") or DEFAULT_TARGET).expanduser().resolve()
    if target.exists():
        print(f"fetch_db: {target} already exists; nothing to do.")
        return

    repo = (os.environ.get("HF_DATASET_REPO") or "").strip().strip("/")
    token = (os.environ.get("HF_TOKEN") or "").strip()
    missing = [name for name, value in (("HF_DATASET_REPO", repo), ("HF_TOKEN", token)) if not value]
    if missing:
        fail(f"{target} does not exist and {' and '.join(missing)} "
             f"{'is' if len(missing) == 1 else 'are'} not set, so it can't be downloaded.")

    url = URL_TEMPLATE.format(repo=urllib.parse.quote(repo, safe="/"))
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    opener = urllib.request.build_opener(_DropAuthOnCrossHostRedirect)

    target.parent.mkdir(parents=True, exist_ok=True)
    # Temp file in the target's own directory so the final move is an atomic rename.
    fd, tmp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".part", dir=str(target.parent))
    tmp_path = Path(tmp_name)
    print(f"fetch_db: downloading {url}")
    try:
        try:
            with os.fdopen(fd, "wb") as out, opener.open(request, timeout=TIMEOUT_SECONDS) as resp:
                size = 0
                while True:
                    chunk = resp.read(CHUNK_BYTES)
                    if not chunk:
                        break
                    out.write(chunk)
                    size += len(chunk)
        except urllib.error.HTTPError as exc:
            hints = {
                401: "HF_TOKEN was rejected, or HF_DATASET_REPO is wrong (private repos answer 401 to both)",
                403: "HF_TOKEN doesn't have read access to this dataset",
                404: "no rule1.db at that path, or HF_DATASET_REPO is wrong",
            }
            hint = hints.get(exc.code)
            fail(f"download failed: HTTP {exc.code} {exc.reason}" + (f" ({hint})." if hint else "."))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            reason = getattr(exc, "reason", exc)
            fail(f"download failed: {reason}")

        if size <= MIN_BYTES:
            fail(f"downloaded file is only {size:,} bytes (expected more than {MIN_BYTES:,}); not installing it.")
        with open(tmp_path, "rb") as f:
            if f.read(len(SQLITE_MAGIC)) != SQLITE_MAGIC:
                fail("downloaded file is not a SQLite database (wrong header); not installing it.")

        os.replace(tmp_path, target)
        print(f"fetch_db: wrote {target} ({size:,} bytes).")
    finally:
        # Left behind only when something above failed.
        if tmp_path.exists():
            tmp_path.unlink()


if __name__ == "__main__":
    main()
