#!/usr/bin/env python3
"""Bounded healthcheck and recovery for the WaterfallHunter stack.

This timer runs once per minute. It intentionally performs only one bounded
restart attempt per unhealthy container and never runs compose, rebuilds an
image, deletes data, or restarts the whole stack. The old implementation only
checked ``.State.Running``: Docker marks a process as Running even when its
healthcheck is ``unhealthy``, so the timer reported the stack healthy while a
dependency was unusable.
"""
import subprocess
import sys
import json

CONTAINERS = [
    "waterfall-backend",
    "waterfall-frontend",
    "waterfall-watchdog",
]

def container_state(name):
    try:
        result = subprocess.run(
            [
                "docker", "inspect", name, "--format",
                "{{.State.Running}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",
            ],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return "missing"
        running, _, health = result.stdout.strip().partition("|")
        if running != "true":
            return "stopped"
        # A container with no Docker healthcheck is running but unverified;
        # it is not treated as a failure because there is no predicate to use.
        return "healthy" if health in {"healthy", "none"} else health or "unknown"
    except Exception:
        return "unknown"


def restart_container(name):
    try:
        result = subprocess.run(
            ["docker", "restart", "--time", "20", name],
            capture_output=True, text=True, timeout=45,
        )
        return result.returncode == 0
    except Exception:
        return False

def main():
    all_ok = True
    status = {}
    for c in CONTAINERS:
        state = container_state(c)
        status[c] = state
        if state != "healthy":
            all_ok = False

    # Also check backend health
    try:
        import urllib.request
        r = urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=5)
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
        "frontend_http": status.get("waterfall-frontend") == "healthy",
    }

    if not all_ok:
        # One bounded restart for a genuinely unhealthy/stopped container.
        # Do not restart on "unknown": an inspect failure must not turn a
        # transient Docker CLI problem into a destructive recovery loop.
        for name, state in status.items():
            if state in {"missing", "stopped", "unhealthy"} and name in CONTAINERS:
                print(f"Attempting bounded restart of {name}...", file=sys.stderr)
                result["services"][name] = f"{state}: restart_attempted={restart_container(name)}"

    print(json.dumps(result))
    sys.exit(0 if all_ok else 1)

if __name__ == "__main__":
    main()
