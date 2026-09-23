"""One-off: set .env DATABASE_URL to Railway public TCP proxy."""
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote, unquote

PUBLIC_HOST = "altaria.proxy.rlwy.net"
PUBLIC_PORT = 36706


def main() -> None:
    import sys

    raw = sys.stdin.read().strip() if not sys.stdin.isatty() else ""
    if not raw:
        raw = subprocess.check_output(["railway", "variables", "--json"], text=True)
    data = json.loads(raw)
    url = data.get("DATABASE_URL", "")
    match = re.match(r"postgresql://([^:]+):([^@]+)@([^/]+)/(.+)", url)
    if not match:
        sys.exit("Could not parse Railway DATABASE_URL")

    user, password, _, db = match.groups()
    password = unquote(password)
    public = f"postgresql://{user}:{quote(password, safe='')}@{PUBLIC_HOST}:{PUBLIC_PORT}/{db}"

    env_path = Path(".env")
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    out: list[str] = []
    replaced = False
    for line in lines:
        if line.startswith("DATABASE_URL="):
            out.append(f"DATABASE_URL={public}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"DATABASE_URL={public}")
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"Updated .env DATABASE_URL -> {PUBLIC_HOST}:{PUBLIC_PORT}")


if __name__ == "__main__":
    main()
