"""Alembic revision chain — no duplicate heads for payment migration."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VERSIONS = REPO_ROOT / "alembic" / "versions"


def test_single_head_revision_0019():
    rev_files = list(VERSIONS.glob("*.py"))
    revisions = {}
    for path in rev_files:
        text = path.read_text(encoding="utf-8")
        rev = None
        down = None
        for line in text.splitlines():
            if line.startswith('revision = "'):
                rev = line.split('"')[1]
            if line.startswith("down_revision = "):
                part = line.split("=", 1)[1].strip().strip('"').strip("'")
                down = part if part != "None" else None
        if rev:
            revisions[rev] = {"file": path.name, "down": down}

    assert "0019" in revisions
    assert revisions["0019"]["down"] == "0018"
    assert revisions["0019"]["file"] == "0019_payment_registration_and_provider_metadata.py"

    assert revisions["0020"]["down"] == "0019"
    assert revisions["0021"]["down"] == "0020"
    heads = [r for r, meta in revisions.items() if r not in {m["down"] for m in revisions.values() if m["down"]}]
    assert heads == ["0021"]
