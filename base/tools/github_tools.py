"""
GitHub REST API tools for the Skogum agent system.

All functions are synchronous and intended to be run via asyncio.to_thread.
Each function creates its own httpx.Client per call (thread-safe).
Authentication via GITHUB_TOKEN env var; default org via GH_ORG env var.
"""

import base64
import json
import os
import shutil
import subprocess
import tempfile
from typing import Any

import httpx

GITHUB_API = "https://api.github.com"
_API_VERSION = "2022-11-28"


# ── Internal helpers ───────────────────────────────────────────────────────────

def _client() -> httpx.Client:
    token = os.environ["GITHUB_TOKEN"]
    return httpx.Client(
        base_url=GITHUB_API,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": _API_VERSION,
        },
        timeout=30.0,
    )


def _default_owner() -> str:
    org = os.environ.get("GH_ORG", "").strip()
    if org:
        return org
    with _client() as client:
        r = client.get("/user")
        r.raise_for_status()
        return r.json()["login"]


def _parse_next_link(link_header: str) -> str | None:
    if not link_header:
        return None
    for part in link_header.split(","):
        part = part.strip()
        if 'rel="next"' in part:
            return part.split(";")[0].strip().strip("<>")
    return None


# ── Tool implementations ───────────────────────────────────────────────────────

def github_create_repo(
    name: str,
    description: str = "",
    private: bool = True,
    org: str | None = None,
) -> dict:
    """Create a GitHub repository in an org or under the authenticated user."""
    owner = org or os.environ.get("GH_ORG", "").strip()
    url = f"/orgs/{owner}/repos" if owner else "/user/repos"
    with _client() as client:
        r = client.post(url, json={"name": name, "description": description, "private": private})
        if r.status_code == 422:
            # Repo already exists — fetch and return it
            repo_owner = owner if owner else _default_owner()
            r2 = client.get(f"/repos/{repo_owner}/{name}")
            r2.raise_for_status()
            data = r2.json()
        else:
            r.raise_for_status()
            data = r.json()
        return {"html_url": data["html_url"], "full_name": data["full_name"], "clone_url": data["clone_url"]}


def github_list_repos(org: str | None = None) -> dict:
    """List repositories in a GitHub org or for the authenticated user."""
    owner = org or os.environ.get("GH_ORG", "").strip()
    url = f"/orgs/{owner}/repos" if owner else "/user/repos"
    results = []
    params: dict = {"per_page": 100, "page": 1}
    with _client() as client:
        while url:
            r = client.get(url, params=params)
            r.raise_for_status()
            for repo in r.json():
                results.append({
                    "name": repo["name"],
                    "full_name": repo["full_name"],
                    "private": repo["private"],
                    "html_url": repo["html_url"],
                })
            url = _parse_next_link(r.headers.get("Link", ""))
            params = {}
    return {"repos": results, "count": len(results)}


def github_create_branch(
    repo: str,
    branch: str,
    from_branch: str = "main",
    org: str | None = None,
) -> dict:
    """Create a new branch in a repository from an existing branch."""
    owner = org or _default_owner()
    with _client() as client:
        r = client.get(f"/repos/{owner}/{repo}/git/ref/heads/{from_branch}")
        r.raise_for_status()
        sha = r.json()["object"]["sha"]
        r2 = client.post(
            f"/repos/{owner}/{repo}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": sha},
        )
        if r2.status_code == 422:
            # Branch already exists — that's fine, just return it
            pass
        else:
            r2.raise_for_status()
        return {"branch": branch, "sha": sha, "from": from_branch}


def github_create_or_update_file(
    repo: str,
    path: str,
    content: str,
    commit_message: str,
    branch: str = "main",
    org: str | None = None,
) -> dict:
    """Create or update a file in a repository. Content is plain text; base64 handled automatically."""
    if content is None:
        raise ValueError("content must be a string, got None")
    owner = org or _default_owner()
    encoded = base64.b64encode(content.encode()).decode()
    with _client() as client:
        # Fetch existing SHA (required for updates)
        sha = None
        r = client.get(f"/repos/{owner}/{repo}/contents/{path}", params={"ref": branch})
        if r.status_code == 200:
            sha = r.json().get("sha")
        elif r.status_code != 404:
            r.raise_for_status()

        body: dict[str, Any] = {
            "message": commit_message,
            "content": encoded,
            "branch": branch,
        }
        if sha:
            body["sha"] = sha

        r2 = client.put(f"/repos/{owner}/{repo}/contents/{path}", json=body)
        r2.raise_for_status()
        data = r2.json()
        return {
            "path": path,
            "html_url": data["content"]["html_url"],
            "commit_sha": data["commit"]["sha"],
        }


def github_get_file(
    repo: str,
    path: str,
    branch: str = "main",
    org: str | None = None,
) -> dict:
    """Get the decoded text content of a file from a repository."""
    owner = org or _default_owner()
    with _client() as client:
        r = client.get(f"/repos/{owner}/{repo}/contents/{path}", params={"ref": branch})
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):
            # Path is a directory — return listing so the agent can navigate
            entries = [{"name": e["name"], "type": e["type"], "path": e["path"]} for e in data]
            return {"path": path, "type": "directory", "entries": entries}
        decoded = base64.b64decode(data["content"]).decode()
        return {"path": path, "content": decoded, "sha": data["sha"]}


def github_create_pr(
    repo: str,
    title: str,
    body: str,
    head_branch: str,
    base_branch: str = "main",
    org: str | None = None,
    closes_issue: int | None = None,
) -> dict:
    """Create a pull request. If closes_issue is set, appends 'Closes #N' to link and auto-close the issue on merge."""
    owner = org or _default_owner()
    if closes_issue:
        body = f"{body}\n\nCloses #{closes_issue}"
    with _client() as client:
        r = client.post(
            f"/repos/{owner}/{repo}/pulls",
            json={"title": title, "body": body, "head": head_branch, "base": base_branch},
        )
        r.raise_for_status()
        data = r.json()
        return {"pr_number": data["number"], "html_url": data["html_url"], "state": data["state"]}


def github_merge_pr(
    repo: str,
    pr_number: int,
    org: str | None = None,
) -> dict:
    """Merge a pull request using squash merge."""
    owner = org or _default_owner()
    with _client() as client:
        r = client.put(
            f"/repos/{owner}/{repo}/pulls/{pr_number}/merge",
            json={"merge_method": "squash"},
        )
        r.raise_for_status()
        data = r.json()
        return {"merged": data.get("merged", False), "sha": data.get("sha"), "message": data.get("message")}


def github_add_secret(
    repo: str,
    secret_name: str,
    secret_value: str,
    org: str | None = None,
) -> dict:
    """Add or update a GitHub Actions secret. Encrypts using the repo's public key."""
    from nacl.public import PublicKey, SealedBox  # deferred — only Infra uses this
    owner = org or _default_owner()
    with _client() as client:
        r = client.get(f"/repos/{owner}/{repo}/actions/secrets/public-key")
        r.raise_for_status()
        key_data = r.json()
        key_id = key_data["key_id"]
        pub_key = PublicKey(base64.b64decode(key_data["key"]))
        encrypted = base64.b64encode(SealedBox(pub_key).encrypt(secret_value.encode())).decode()
        r2 = client.put(
            f"/repos/{owner}/{repo}/actions/secrets/{secret_name}",
            json={"encrypted_value": encrypted, "key_id": key_id},
        )
        r2.raise_for_status()
        return {"secret_name": secret_name, "repo": f"{owner}/{repo}", "status": "created_or_updated"}


def github_create_issue(
    repo: str,
    title: str,
    body: str = "",
    org: str | None = None,
) -> dict:
    """Create a GitHub issue."""
    owner = org or _default_owner()
    with _client() as client:
        r = client.post(
            f"/repos/{owner}/{repo}/issues",
            json={"title": title, "body": body},
        )
        r.raise_for_status()
        data = r.json()
        return {"issue_number": data["number"], "html_url": data["html_url"]}


def github_list_issues(
    repo: str,
    org: str | None = None,
    state: str = "open",
) -> dict:
    """List issues in a repository (excludes pull requests)."""
    owner = org or _default_owner()
    results = []
    params: dict = {"state": state, "per_page": 50, "page": 1}
    url: str | None = f"/repos/{owner}/{repo}/issues"
    with _client() as client:
        while url:
            r = client.get(url, params=params)
            r.raise_for_status()
            for issue in r.json():
                if "pull_request" in issue:
                    continue  # skip PRs which also appear in /issues
                results.append({
                    "number": issue["number"],
                    "title": issue["title"],
                    "body": issue.get("body", ""),
                    "state": issue["state"],
                    "html_url": issue["html_url"],
                    "labels": [l["name"] for l in issue.get("labels", [])],
                    "created_at": issue["created_at"],
                })
            url = _parse_next_link(r.headers.get("Link", ""))
            params = {}
    return {"issues": results, "count": len(results)}


def github_get_issue(
    repo: str,
    issue_number: int,
    org: str | None = None,
) -> dict:
    """Get details of a specific issue."""
    owner = org or _default_owner()
    with _client() as client:
        r = client.get(f"/repos/{owner}/{repo}/issues/{issue_number}")
        r.raise_for_status()
        issue = r.json()
        return {
            "number": issue["number"],
            "title": issue["title"],
            "body": issue.get("body", ""),
            "state": issue["state"],
            "html_url": issue["html_url"],
            "labels": [l["name"] for l in issue.get("labels", [])],
        }


def github_create_issue_comment(
    repo: str,
    issue_number: int,
    body: str,
    org: str | None = None,
) -> dict:
    """Post a comment on a GitHub issue."""
    owner = org or _default_owner()
    with _client() as client:
        r = client.post(
            f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
            json={"body": body},
        )
        r.raise_for_status()
        data = r.json()
        return {"comment_id": data["id"], "html_url": data["html_url"]}


def github_list_workflow_runs(
    repo: str,
    status: str = "failure",
    org: str | None = None,
    limit: int = 10,
) -> dict:
    """List recent GitHub Actions workflow runs filtered by status (failure, success, etc.)."""
    owner = org or _default_owner()
    with _client() as client:
        r = client.get(
            f"/repos/{owner}/{repo}/actions/runs",
            params={"status": status, "per_page": limit},
        )
        r.raise_for_status()
        runs = []
        for run in r.json().get("workflow_runs", []):
            runs.append({
                "id": run["id"],
                "name": run["name"],
                "status": run["status"],
                "conclusion": run["conclusion"],
                "head_branch": run["head_branch"],
                "head_sha": run["head_sha"][:12],
                "html_url": run["html_url"],
                "created_at": run["created_at"],
                "updated_at": run["updated_at"],
            })
        return {"runs": runs, "count": len(runs)}


def github_get_failed_job_logs(
    repo: str,
    run_id: int,
    org: str | None = None,
    max_lines: int = 50,
) -> dict:
    """Get logs from failed steps in a GitHub Actions workflow run.
    Returns the last max_lines lines from each failed job step, focused on error output."""
    import io
    import zipfile

    owner = org or _default_owner()
    with _client() as client:
        # Get jobs for this run
        r = client.get(f"/repos/{owner}/{repo}/actions/runs/{run_id}/jobs")
        r.raise_for_status()
        jobs = r.json().get("jobs", [])

        failed_jobs = [j for j in jobs if j.get("conclusion") == "failure"]
        if not failed_jobs:
            return {"run_id": run_id, "error": "No failed jobs found"}

        # Fetch the log zip
        log_r = client.get(
            f"/repos/{owner}/{repo}/actions/runs/{run_id}/logs",
            follow_redirects=True,
        )
        log_r.raise_for_status()

        results = []
        with zipfile.ZipFile(io.BytesIO(log_r.content)) as zf:
            for job in failed_jobs:
                job_name = job["name"]
                # Find the zip entry for this job
                matching = [n for n in zf.namelist() if job_name in n or n.endswith(".txt")]
                # Pick the best match or fall back to first .txt
                entry = next((n for n in zf.namelist() if job_name in n), None)
                if not entry:
                    entry = next((n for n in zf.namelist() if n.endswith(".txt")), None)
                if not entry:
                    continue

                log_text = zf.read(entry).decode(errors="replace")
                lines = log_text.splitlines()

                # Extract error-relevant lines: last N lines + any line with error keywords
                error_keywords = ("error", "Error", "ERROR", "failed", "FAILED", "exit code")
                error_lines = [l for l in lines if any(k in l for k in error_keywords)]
                tail_lines = lines[-max_lines:]

                # Deduplicate while preserving order
                seen = set()
                combined = []
                for l in error_lines + tail_lines:
                    if l not in seen:
                        seen.add(l)
                        combined.append(l)

                results.append({
                    "job_name": job_name,
                    "conclusion": job["conclusion"],
                    "log_excerpt": "\n".join(combined[-max_lines:]),
                })

        return {"run_id": run_id, "repo": f"{owner}/{repo}", "failed_jobs": results}


def build_and_test(
    repo: str,
    branch: str = "main",
    org: str | None = None,
) -> dict:
    """Clone a repo branch and run npm run build to verify it compiles.
    Returns {"success": True} or {"success": False, "step": "...", "output": "...error lines..."}.
    Automatically generates and pushes package-lock.json to the branch if missing.
    Call this after editing source files and before pushing to confirm the build passes.
    """
    owner = org or _default_owner()
    token = os.environ["GITHUB_TOKEN"]
    clone_url = f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"

    tmpdir = tempfile.mkdtemp(prefix="skogum-build-")
    try:
        # Clone
        r = subprocess.run(
            ["git", "clone", "--depth=1", "--branch", branch, clone_url, tmpdir],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode != 0:
            return {"success": False, "step": "clone", "output": r.stderr[-2000:]}

        lock_file = os.path.join(tmpdir, "package-lock.json")
        has_lock = os.path.exists(lock_file)

        # Install deps — prefer npm ci (fast, strict), fall back to npm install
        # when there's no lock file or the lock file is out of sync with package.json
        need_install = False
        if has_lock:
            r = subprocess.run(
                ["npm", "ci", "--legacy-peer-deps"],
                capture_output=True, text=True, timeout=180, cwd=tmpdir,
            )
            if r.returncode != 0 and "EUSAGE" in (r.stdout + r.stderr):
                # Lock file exists but is out of sync — fall back to npm install
                need_install = True
            elif r.returncode != 0:
                return {"success": False, "step": "npm_ci", "output": (r.stdout + r.stderr)[-2000:]}
        else:
            need_install = True

        if need_install:
            r = subprocess.run(
                ["npm", "install", "--legacy-peer-deps"],
                capture_output=True, text=True, timeout=180, cwd=tmpdir,
            )
            if r.returncode != 0:
                return {"success": False, "step": "npm_install", "output": (r.stdout + r.stderr)[-2000:]}

        # Push generated/updated lock file back to the branch
        if (not has_lock or need_install) and os.path.exists(lock_file):
            with open(lock_file) as f:
                lock_content = f.read()
            try:
                github_create_or_update_file(
                    repo=repo,
                    path="package-lock.json",
                    content=lock_content,
                    commit_message="chore: add package-lock.json [generated by build_and_test]",
                    branch=branch,
                    org=org,
                )
            except Exception:
                pass  # non-fatal — build can still proceed

        # Build
        r = subprocess.run(
            ["npm", "run", "build"],
            capture_output=True, text=True, timeout=180, cwd=tmpdir,
        )
        if r.returncode != 0:
            output = r.stdout + r.stderr
            lines = output.splitlines()
            error_lines = [l for l in lines if any(k in l for k in ("Error", "error", "×", "failed", "exit code"))]
            tail = lines[-40:]
            combined = list(dict.fromkeys(error_lines + tail))
            return {"success": False, "step": "build", "output": "\n".join(combined[-50:])}

        return {"success": True, "output": "Build succeeded — safe to push."}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
