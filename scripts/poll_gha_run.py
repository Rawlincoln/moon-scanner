import json
import subprocess
import time
import urllib.request

proc = subprocess.run(
    ["git", "credential", "fill"],
    input="protocol=https\nhost=github.com\n\n",
    text=True,
    capture_output=True,
)
token = ""
for line in proc.stdout.splitlines():
    if line.startswith("password="):
        token = line.split("=", 1)[1]


def gh(url: str):
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "moon-scanner",
        },
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read().decode())


run_id = 30748233050
for i in range(30):
    data = gh(f"https://api.github.com/repos/Rawlincoln/moon-scanner/actions/runs/{run_id}")
    status = data.get("status")
    conclusion = data.get("conclusion")
    print(f"try {i+1} status={status} conclusion={conclusion}")
    if status == "completed":
        print("url", data.get("html_url"))
        jobs = gh(
            f"https://api.github.com/repos/Rawlincoln/moon-scanner/actions/runs/{run_id}/jobs"
        )
        for j in jobs.get("jobs", []):
            print("job", j.get("name"), j.get("conclusion"))
            for s in j.get("steps", []):
                print(" step", s.get("name"), s.get("conclusion"))
        raise SystemExit(0 if conclusion == "success" else 1)
    time.sleep(10)
print("timeout")
raise SystemExit(2)
