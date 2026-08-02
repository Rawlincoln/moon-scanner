"""Set GitHub Actions secrets for 24/7 Telegram cron (uses git credentials)."""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

try:
    from nacl import encoding, public
except ImportError:
    print("Installing PyNaCl...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "PyNaCl", "-q"])
    from nacl import encoding, public


def git_token() -> tuple[str, str]:
    proc = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        text=True,
        capture_output=True,
        check=False,
    )
    user, token = "", ""
    for line in proc.stdout.splitlines():
        if line.startswith("password="):
            token = line.split("=", 1)[1]
        if line.startswith("username="):
            user = line.split("=", 1)[1]
    if not token:
        raise SystemExit("No GitHub token from git credential fill")
    return user, token


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    path = Path(__file__).resolve().parent.parent / ".env"
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def encrypt_secret(public_key_b64: str, secret_value: str) -> str:
    pk = public.PublicKey(public_key_b64.encode("utf-8"), encoding.Base64Encoder())
    sealed = public.SealedBox(pk)
    encrypted = sealed.encrypt(secret_value.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")


def gh(token: str, method: str, url: str, data: dict | None = None):
    req = urllib.request.Request(
        url,
        data=None if data is None else json.dumps(data).encode(),
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "moon-scanner-setup",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read().decode()
            return resp.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()}


def main() -> int:
    user, token = git_token()
    print(f"github_user={user}")
    env = load_env()
    secrets = {
        "TELEGRAM_BOT_TOKEN": env.get("TELEGRAM_BOT_TOKEN", ""),
        "TELEGRAM_CHAT_ID": env.get("TELEGRAM_CHAT_ID", ""),
        "TELEGRAM_CRON_SECRET": env.get("TELEGRAM_CRON_SECRET", ""),
    }
    for k, v in secrets.items():
        print(f"{k}_set={bool(v)}")

    owner, repo = "Rawlincoln", "moon-scanner"
    status, keyinfo = gh(
        token,
        "GET",
        f"https://api.github.com/repos/{owner}/{repo}/actions/secrets/public-key",
    )
    print(f"public_key_status={status}")
    if status != 200:
        print(keyinfo)
        return 1

    key_id = keyinfo["key_id"]
    pubkey = keyinfo["key"]
    for name, value in secrets.items():
        if not value:
            print(f"skip {name}")
            continue
        enc = encrypt_secret(pubkey, value)
        st, res = gh(
            token,
            "PUT",
            f"https://api.github.com/repos/{owner}/{repo}/actions/secrets/{name}",
            {"encrypted_value": enc, "key_id": key_id},
        )
        print(f"secret {name} status={st}")
        if st not in (201, 204):
            print(res)
            return 1

    st, res = gh(
        token,
        "POST",
        f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/telegram-24-7.yml/dispatches",
        {"ref": "main"},
    )
    print(f"workflow_dispatch status={st}")
    if st not in (204, 200):
        print(res)
        # workflow file might need a moment after push
        return 1 if st >= 400 else 0

    st, res = gh(
        token,
        "GET",
        f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/telegram-24-7.yml/runs?per_page=3",
    )
    print(f"runs_status={st}")
    if st == 200:
        for r in res.get("workflow_runs", [])[:3]:
            print(
                f"run id={r.get('id')} status={r.get('status')} "
                f"conclusion={r.get('conclusion')} url={r.get('html_url')}"
            )
    print("OK: GitHub Actions free cron every 5 minutes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
