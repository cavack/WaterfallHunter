#!/usr/bin/env python3
"""Simple healthcheck for standalone Docker containers."""
import subprocess
import sys
import json

CONTAINERS = [
    "waterfall-backend",
    "waterfall-frontend",
    "waterfall-watchdog",
]

def check_container(name):
    try:
        result = subprocess.run(
            ["docker", "inspect", name, "--format", "{{.State.Running}}"],
            capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0 and result.stdout.strip() == "true"
    except Exception:
        return False

def main():
    all_ok = True
    status = {}
    for c in CONTAINERS:
        running = check_container(c)
        status[c] = "healthy" if running else "missing"
        if not running:
            all_ok = False

    # Also check backend health
    try:
        import urllib.request
        r = urllib.request.urlopen("http://172.19.0.2:8000/api/health", timeout=5)
        if r.status != 200:
            all_ok = False
            status["backend_api"] = "unhealthy"
        else:
            status["backend_api"] = "healthy"
    except Exception:
        # Try localhost
        try:
            r = urllib.request.urlopen("http://localhost:3000/api/health", timeout=5)
            if r.status == 200:
                status["backend_api"] = "healthy"
            else:
                status["backend_api"] = "unhealthy"
                all_ok = False
        except Exception:
            status["backend_api"] = "unreachable"
            all_ok = False

    result = {
        "healthy": all_ok,
        "services": status,
        "frontend_http": check_container("waterfall-frontend"),
    }

    if not all_ok:
        # Try to restart missing containers
        for name, state in status.items():
            if state == "missing" and name in CONTAINERS:
                print(f"Attempting restart of {name}...")
                subprocess.run(["docker", "start", name], timeout=10)

    print(json.dumps(result))
    sys.exit(0 if all_ok else 1)

if __name__ == "__main__":
    main()
