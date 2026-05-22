"""
Project Manager agent — mistral-large-latest

Receives raw assignments, produces a task plan via Mistral, initialises the
whiteboard, dispatches subtasks to specialist agents, tracks completion, and
writes a final summary.

Incoming event types: assignment, task_complete, task_failed
"""

import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone

sys.path.insert(0, "/app")
from base_agent import BaseAgent, _now  # noqa: E402
from tools import GITHUB_TOOL_FNS, GITHUB_TOOL_SCHEMAS  # noqa: E402

SYSTEM_PROMPT = """\
You are the Project Manager of Skogum AI Consulting — a senior technical lead
with deep expertise in DevOps, cloud infrastructure, and software architecture.
You are decisive, structured, and proactive.

Your responsibilities:
1. Analyse incoming assignments and decompose them into concrete, ordered tasks.
2. Delegate each task to the right specialist:
   - researcher: market research, documentation, competitive analysis
   - frontend: ALL Next.js, React, Vue, HTML/CSS, UI components, animations, design systems, and web pages
   - engineer: backend APIs, CI/CD pipelines, Dockerfiles, Kubernetes, GitHub Actions, non-UI code
   - qa: testing, validation, code review, bug reports
   - infra: cloud infrastructure, secrets, deployments, DNS, Docker builds, AWS/GCP config
3. Monitor progress via the shared whiteboard.
4. Synthesise results into a clear final deliverable summary.

When asked to produce a task plan, respond with **JSON only** (no prose), using
this exact schema:

{
  "plan_summary": "<one-sentence overview>",
  "tasks": [
    {
      "id": "<short_snake_case_id>",
      "type": "research | implement | validate | deploy",
      "assigned_to": "researcher | engineer | frontend | qa | infra",
      "description": "<specific, actionable description with a clear definition of done>",
      "depends_on": ["<task_id>", ...]   // empty list if no deps
    }
  ]
}

Ensure tasks are ordered so dependencies form a DAG (no cycles).

STRICT RULES — never violate:
- NEVER plan a Vercel deployment task. Skogum does not use Vercel.
- Deployment means building a Docker image. Assign Docker build tasks to infra.
"""

# Agents that need to receive the full assignment context in their payload
_CONTEXT_AGENTS = {"researcher", "engineer", "qa", "infra"}


_PM_TOOLS = {
    "github_list_repos",
    "github_create_issue",
    "github_create_issue_comment",
    "github_list_issues",
    "github_get_issue",
    "github_merge_pr",
}


class ProjectManagerAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(
            agent_name="project_manager",
            model=os.getenv("PM_MODEL", "mistral-large-latest"),
            system_prompt=SYSTEM_PROMPT,
        )
        for schema in GITHUB_TOOL_SCHEMAS:
            if schema["name"] in _PM_TOOLS:
                self.register_tool(schema["name"], GITHUB_TOOL_FNS[schema["name"]], schema)
        # assignment_id → { plan, completed: set, failed: set, assignment_text }
        self._active: dict[str, dict] = {}

    # ── Event routing ──────────────────────────────────────────────────────────

    async def handle_event(self, event: dict) -> None:
        t = event.get("type")
        if t == "assignment":
            await self._on_assignment(event)
        elif t == "task_complete":
            await self._on_task_complete(event)
        elif t == "task_failed":
            await self._on_task_failed(event)
        elif t == "github_issue":
            await self._on_github_issue(event)
        else:
            self.logger.warning("Unknown event type: %s", t)

    # ── Assignment intake ──────────────────────────────────────────────────────

    async def _on_assignment(self, event: dict) -> None:
        assignment_id = event.get("task_id") or str(uuid.uuid4())
        description = event.get("payload", {}).get("description", "")
        self.logger.info("New assignment [%s]: %.120s", assignment_id, description)

        await self.write_whiteboard(assignment_id, "assignment", description)
        await self.write_whiteboard(assignment_id, "status", "planning")
        await self.write_whiteboard(assignment_id, "created_at", _now())

        plan = await self._build_plan(assignment_id, description)

        await self.write_whiteboard(assignment_id, "plan", json.dumps(plan, indent=2))
        await self.write_whiteboard(assignment_id, "plan_summary", plan.get("plan_summary", ""))
        await self.write_whiteboard(assignment_id, "status", "in_progress")

        self._active[assignment_id] = {
            "plan": plan,
            "completed": set(),
            "failed": set(),
            "retries": {},  # task_plan_id → retry count (PM-owned, not from event payload)
            "assignment_text": description,
            # maps task_id → assigned_to so retry can route correctly
            "task_agents": {t["id"]: t["assigned_to"] for t in plan.get("tasks", [])},
        }

        # Dispatch tasks that have no unmet dependencies
        for task in plan.get("tasks", []):
            if not task.get("depends_on"):
                await self._dispatch(assignment_id, task)

    async def _build_plan(self, assignment_id: str, description: str) -> dict:
        raw = await self.call_mistral(
            [
                {
                    "role": "user",
                    "content": (
                        f"Produce a task plan for this DevOps assignment:\n\n"
                        f"{description}\n\n"
                        "Respond with JSON only."
                    ),
                }
            ]
        )
        try:
            text = raw.strip()
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            return json.loads(text.strip())
        except (json.JSONDecodeError, IndexError):
            self.logger.error("Could not parse plan JSON — using fallback plan")
            return _default_plan(description)

    # ── Task dispatch ──────────────────────────────────────────────────────────

    async def _dispatch(self, assignment_id: str, task: dict) -> None:
        assigned_to = task["assigned_to"]
        task_id = f"{assignment_id}/{task['id']}"

        # Thread any accumulated GitHub context into the task description
        whiteboard = await self.read_whiteboard(assignment_id)
        github_repo = whiteboard.get("github_repo", "")
        github_branch = whiteboard.get("github_branch", "")
        context_parts = []
        if github_repo:
            context_parts.append(f"GitHub repo: {github_repo}")
        if github_branch:
            context_parts.append(f"feature branch: {github_branch}")
        context_note = ("\n\nNote: " + ", ".join(context_parts) + ".") if context_parts else ""

        payload: dict = {
            "task_plan_id": task["id"],
            "description": task["description"] + context_note,
            "assignment": self._active[assignment_id]["assignment_text"],
        }
        # Tell QA which implement task to re-trigger on failure (avoids hardcoding).
        # For validate tasks with explicit deps, use the first dep.
        # For validate tasks with no deps (e.g. validate-first plans), fall back
        # to the first implement task in the plan.
        if task.get("type") == "validate":
            if task.get("depends_on"):
                payload["impl_task_id"] = task["depends_on"][0]
            else:
                impl_tasks = [
                    t["id"] for t in self._active[assignment_id]["plan"].get("tasks", [])
                    if t.get("type") == "implement"
                ]
                if impl_tasks:
                    payload["impl_task_id"] = impl_tasks[0]

        event = {
            "task_id": task_id,
            "assignment_id": assignment_id,
            "type": task["type"],
            "assigned_to": assigned_to,
            "payload": payload,
            "status": "pending",
        }

        await self.emit_event(assigned_to, event)
        await self.write_whiteboard(assignment_id, f"task_{task['id']}_status", "dispatched")
        self.logger.info("Dispatched %s → %s", task["id"], assigned_to)

    # ── Completion tracking ────────────────────────────────────────────────────

    async def _on_task_complete(self, event: dict) -> None:
        assignment_id = event.get("assignment_id", "")
        task_plan_id = event.get("payload", {}).get("task_plan_id", "")
        self.logger.info("Task complete: %s / %s", assignment_id, task_plan_id)

        state = self._active.get(assignment_id)
        if not state:
            return

        await self.write_whiteboard(assignment_id, f"task_{task_plan_id}_status", "completed")
        state["completed"].add(task_plan_id)

        # Check whether completing this task unlocks dependent tasks
        for task in state["plan"].get("tasks", []):
            deps = set(task.get("depends_on", []))
            tid = task["id"]
            if (
                deps
                and deps.issubset(state["completed"])
                and tid not in state["completed"]
                and tid not in state["failed"]
            ):
                await self._dispatch(assignment_id, task)

        await self._check_done(assignment_id)

    MAX_TASK_RETRIES = int(os.getenv("MAX_TASK_RETRIES", "3"))

    async def _on_task_failed(self, event: dict) -> None:
        assignment_id = event.get("assignment_id", "")
        task_plan_id = event.get("payload", {}).get("task_plan_id", "")
        reason = event.get("payload", {}).get("reason", "unknown")

        state = self._active.get(assignment_id)
        retry = state["retries"].get(task_plan_id, 0) if state else self.MAX_TASK_RETRIES

        self.logger.error("Task failed: %s / %s — %s (pm_retry=%d)", assignment_id, task_plan_id, reason, retry)
        await self.write_whiteboard(
            assignment_id, f"task_{task_plan_id}_status", f"failed({retry}): {reason[:120]}"
        )
        await self.write_whiteboard(assignment_id, f"task_{task_plan_id}_retry_count", str(retry))

        if retry < self.MAX_TASK_RETRIES:
            original_task = next(
                (t for t in (state["plan"].get("tasks", []) if state else [])
                 if t["id"] == task_plan_id),
                None,
            )
            if original_task is None:
                self.logger.error(
                    "Cannot retry %s — task not found in plan for %s.",
                    task_plan_id, assignment_id,
                )
                return

            state["retries"][task_plan_id] = retry + 1
            self.logger.info("Retrying %s (pm_attempt %d/%d)", task_plan_id, retry + 1, self.MAX_TASK_RETRIES)

            whiteboard = await self.read_whiteboard(assignment_id)
            github_repo = whiteboard.get("github_repo", "")
            github_branch = whiteboard.get("github_branch", "")
            retry_desc = original_task["description"]
            retry_parts = []
            if github_repo:
                retry_parts.append(f"GitHub repo: {github_repo}")
            if github_branch:
                retry_parts.append(f"feature branch: {github_branch}")
            if retry_parts:
                retry_desc += "\n\nNote: " + ", ".join(retry_parts) + "."

            retry_event = {
                **event,
                "type": original_task["type"],
                "assigned_to": original_task["assigned_to"],
                "status": "retry",
                "payload": {
                    **event.get("payload", {}),
                    "description": retry_desc,
                    "retry_count": retry + 1,
                    "failure_reason": reason,
                },
            }
            await self.emit_event(original_task["assigned_to"], retry_event)
        else:
            if state:
                state["failed"].add(task_plan_id)
            await self.write_whiteboard(assignment_id, "status", "failed")
            self.logger.error(
                "Assignment %s failed — task %s exhausted %d retries",
                assignment_id, task_plan_id, self.MAX_TASK_RETRIES,
            )

    # ── GitHub issue ingestion ─────────────────────────────────────────────────

    async def poll_workflow_runs(self) -> None:
        """Background loop: poll all org repos every 5 minutes for failed workflow runs and auto-dispatch fixes."""
        from tools import GITHUB_TOOL_FNS
        list_repos_fn = GITHUB_TOOL_FNS["github_list_repos"]
        list_runs_fn = GITHUB_TOOL_FNS["github_list_workflow_runs"]
        get_logs_fn = GITHUB_TOOL_FNS["github_get_failed_job_logs"]

        _EXCLUDED_REPOS = {"cicd-pipeline-demo", "debug-env-file-issue"}
        self.logger.info("GitHub workflow run poller started (interval=30s)")
        while True:
            await asyncio.sleep(30)
            try:
                repos = await asyncio.to_thread(list_repos_fn)
                for repo in repos.get("repos", []):
                    repo_name = repo["name"]
                    if repo_name in _EXCLUDED_REPOS:
                        continue
                    runs = await asyncio.to_thread(list_runs_fn, repo_name, "failure", None, 1)
                    for run in runs.get("runs", []):
                        if run.get("head_branch") != "main":
                            continue  # ignore PR/feature branch failures — only fix main
                        key = f"{repo_name}#run{run['id']}"
                        seen = await self.valkey.sismember("seen_workflow_runs", key)
                        if seen:
                            continue
                        await self.valkey.sadd("seen_workflow_runs", key)
                        self.logger.info("Failed workflow run: %s — %s (%s)", key, run["name"], run["html_url"])

                        # Fetch logs to extract the error
                        try:
                            log_data = await asyncio.to_thread(get_logs_fn, repo_name, run["id"])
                            failed_jobs = log_data.get("failed_jobs", [])
                            log_excerpt = "\n\n".join(
                                f"Job: {j['job_name']}\n{j['log_excerpt']}"
                                for j in failed_jobs
                            ) if failed_jobs else "(could not retrieve logs)"
                        except Exception as exc:
                            self.logger.warning("Could not fetch logs for run %s: %s", run["id"], exc)
                            log_excerpt = "(log fetch failed)"

                        description = (
                            f"Fix a failed GitHub Actions workflow run in repo '{repo_name}'.\n\n"
                            f"Workflow: {run['name']}\n"
                            f"Branch: {run['head_branch']}\n"
                            f"Commit: {run['head_sha']}\n"
                            f"Run URL: {run['html_url']}\n\n"
                            f"Failed job log excerpt:\n```\n{log_excerpt}\n```\n\n"
                            f"Identify the root cause from the log and fix the relevant file(s) in the repo. "
                            f"Push the fix directly to {run['head_branch']}.\n\n"
                            f"STRICT RULES — do NOT violate:\n"
                            f"- NEVER create or modify any file under .github/workflows/\n"
                            f"- The only workflow file is deploy.yml and it must not be touched\n"
                            f"- NEVER add a .env file to the repo\n"
                            f"- Fix only application code (Dockerfile, package.json, source files)\n"
                        )

                        assignment_id = f"workflow-{repo_name}-{run['id']}"
                        await self.emit_event(
                            "project_manager",
                            {
                                "task_id": assignment_id,
                                "type": "assignment",
                                "assigned_to": "project_manager",
                                "payload": {"description": description},
                                "status": "pending",
                                "timestamp": _now(),
                            },
                        )
                        self.logger.info("Auto-dispatched fix assignment for workflow run %s", run["id"])
            except Exception:
                self.logger.exception("Error polling workflow runs")

    async def poll_github_issues(self) -> None:
        """Background loop: poll all org repos every 5 minutes for new open issues."""
        from tools import GITHUB_TOOL_FNS
        list_repos_fn = GITHUB_TOOL_FNS["github_list_repos"]
        list_issues_fn = GITHUB_TOOL_FNS["github_list_issues"]
        comment_fn = GITHUB_TOOL_FNS["github_create_issue_comment"]

        self.logger.info("GitHub issue poller started (interval=300s)")
        while True:
            await asyncio.sleep(300)
            try:
                repos = await asyncio.to_thread(list_repos_fn)
                for repo in repos.get("repos", []):
                    repo_name = repo["name"]
                    issues = await asyncio.to_thread(list_issues_fn, repo_name)
                    for issue in issues.get("issues", []):
                        key = f"{repo_name}#{issue['number']}"
                        seen = await self.valkey.sismember("seen_issues", key)
                        if seen:
                            continue
                        await self.valkey.sadd("seen_issues", key)
                        self.logger.info("New issue detected: %s — %s", key, issue["title"])
                        # Acknowledge on GitHub
                        try:
                            await asyncio.to_thread(
                                comment_fn, repo_name, issue["number"],
                                "👋 Acknowledged by Skogum R&D. Triaging and dispatching a fix shortly.",
                            )
                        except Exception:
                            pass
                        # Push into our own event queue for processing
                        await self.emit_event(
                            "project_manager",
                            {
                                "task_id": f"issue-{repo_name}-{issue['number']}",
                                "type": "github_issue",
                                "assigned_to": "project_manager",
                                "payload": {
                                    "repo": repo_name,
                                    "issue_number": issue["number"],
                                    "title": issue["title"],
                                    "body": issue.get("body", ""),
                                    "html_url": issue["html_url"],
                                },
                            },
                        )
            except Exception:
                self.logger.exception("Error polling GitHub issues")

    async def _on_github_issue(self, event: dict) -> None:
        """Classify an incoming GitHub issue and dispatch a fix task to the right agent."""
        payload = event.get("payload", {})
        repo = payload.get("repo", "")
        issue_number = payload.get("issue_number", 0)
        title = payload.get("title", "")
        body = payload.get("body", "")
        html_url = payload.get("html_url", "")

        self.logger.info("Processing issue #%d in %s: %s", issue_number, repo, title)

        # Ask Mistral which agent should fix this
        classification = await self.call_mistral([{
            "role": "user",
            "content": (
                f"A GitHub issue was filed in repo '{repo}':\n\n"
                f"Title: {title}\n"
                f"Body: {body[:500]}\n\n"
                "Which agent should fix this? Reply with ONLY one word: "
                "frontend, engineer, qa, infra, or researcher."
            ),
        }])
        agent = classification.strip().lower().split()[0]
        if agent not in {"frontend", "engineer", "qa", "infra", "researcher"}:
            agent = "engineer"

        self.logger.info("Issue #%d routed to %s", issue_number, agent)

        assignment_id = f"issue-{repo}-{issue_number}"
        description = (
            f"Fix GitHub issue #{issue_number} in repo '{repo}'.\n\n"
            f"Issue title: {title}\n"
            f"Issue body: {body}\n"
            f"Issue URL: {html_url}\n\n"
            f"Push the fix to a new branch, open a PR with closes_issue={issue_number} "
            f"so it is linked and auto-closed when merged."
        )

        await self.write_whiteboard(assignment_id, "assignment", description)
        await self.write_whiteboard(assignment_id, "status", "in_progress")
        await self.write_whiteboard(assignment_id, "github_repo", html_url.split("/issues/")[0])
        await self.write_whiteboard(assignment_id, "created_at", _now())

        self._active[assignment_id] = {
            "plan": {"tasks": [{"id": "fix_issue", "assigned_to": agent, "type": "implement", "description": description, "depends_on": []}]},
            "completed": set(),
            "failed": set(),
            "assignment_text": description,
            "task_agents": {"fix_issue": agent},
        }

        await self._dispatch(assignment_id, self._active[assignment_id]["plan"]["tasks"][0])

    async def _check_done(self, assignment_id: str) -> None:
        state = self._active.get(assignment_id)
        if not state:
            return

        total = len(state["plan"].get("tasks", []))
        done = len(state["completed"])
        if done < total:
            return

        self.logger.info("Assignment %s COMPLETE (%d/%d tasks)", assignment_id, done, total)
        await self.write_whiteboard(assignment_id, "status", "completed")
        await self.write_whiteboard(assignment_id, "completed_at", _now())

        whiteboard = await self.read_whiteboard(assignment_id)
        summary = await self.call_mistral(
            [
                {
                    "role": "user",
                    "content": (
                        "Write a concise completion summary for the client. "
                        "Include what was built, key decisions made, and next steps.\n\n"
                        f"Whiteboard:\n{json.dumps(whiteboard, indent=2)}"
                    ),
                }
            ]
        )
        await self.write_whiteboard(assignment_id, "summary", summary)
        self.logger.info("Summary written for %s", assignment_id)


    async def _restore_active_assignments(self) -> None:
        """On startup, reload in-progress assignments from Valkey whiteboards."""
        keys = await self.valkey.keys("whiteboard:*")
        restored = 0
        for key in keys:
            assignment_id = key.removeprefix("whiteboard:")
            # Skip workflow-run whiteboards (they use a different key format)
            if assignment_id.startswith("workflow-"):
                continue
            wb = await self.read_whiteboard(assignment_id)
            if wb.get("status") != "in_progress":
                continue
            try:
                plan = json.loads(wb.get("plan", "{}"))
            except json.JSONDecodeError:
                continue
            if not plan.get("tasks"):
                continue
            completed = {
                t["id"] for t in plan["tasks"]
                if wb.get(f"task_{t['id']}_status") == "completed"
            }
            failed = {
                t["id"] for t in plan["tasks"]
                if str(wb.get(f"task_{t['id']}_status", "")).startswith("failed")
            }
            retries = {
                t["id"]: int(wb.get(f"task_{t['id']}_retry_count", 0))
                for t in plan["tasks"]
            }
            self._active[assignment_id] = {
                "plan": plan,
                "completed": completed,
                "failed": failed,
                "retries": retries,
                "assignment_text": wb.get("assignment", ""),
                "task_agents": {t["id"]: t["assigned_to"] for t in plan["tasks"]},
            }
            restored += 1
            self.logger.info("Restored assignment %s (%d completed, %d failed)", assignment_id, len(completed), len(failed))
        if restored:
            self.logger.info("Restored %d active assignment(s) from whiteboard", restored)

    async def run(self) -> None:
        self.logger.info("Agent %r starting (model=%s)", self.agent_name, self.model)
        await self._restore_active_assignments()
        await asyncio.gather(
            self.listen_events(),
            self.listen_discussions(),
            self.poll_github_issues(),
            self.poll_workflow_runs(),
        )


# ── Fallback plan (used when Mistral returns un-parseable JSON) ────────────────

def _default_plan(description: str) -> dict:
    return {
        "plan_summary": f"Complete assignment: {description[:80]}",
        "tasks": [
            {
                "id": "research_1",
                "type": "research",
                "assigned_to": "researcher",
                "description": f"Research best practices and relevant documentation for: {description}",
                "depends_on": [],
            },
            {
                "id": "implement_1",
                "type": "implement",
                "assigned_to": "engineer",
                "description": f"Implement the solution for: {description}. Use researcher findings from whiteboard.",
                "depends_on": ["research_1"],
            },
            {
                "id": "validate_1",
                "type": "validate",
                "assigned_to": "qa",
                "description": "Validate the implementation for correctness, security, and best practices.",
                "depends_on": ["implement_1"],
            },
            {
                "id": "build_1",
                "type": "implement",
                "assigned_to": "infra",
                "description": "Build a Docker image for the implementation and verify it runs correctly.",
                "depends_on": ["validate_1"],
            },
        ],
    }


if __name__ == "__main__":
    asyncio.run(ProjectManagerAgent().run())
