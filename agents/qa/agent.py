"""
QA agent — devstral-latest

Validates implementations via static code analysis only.
On pass → notifies PM. On fail → emits task_failed back to PM so it can retry.

Retry counter is stored in the whiteboard so it survives across re-dispatch cycles.
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, "/app")
from base_agent import BaseAgent, _now  # noqa: E402
from tools import GITHUB_TOOL_FNS, GITHUB_TOOL_SCHEMAS  # noqa: E402

SYSTEM_PROMPT = """\
You are the QA Lead at Skogum AI Consulting. You validate implementations using
GitHub tools to read committed files AND the build_and_test tool to verify the
build actually compiles without errors.

ALWAYS run build_and_test on any repository that has a package.json. A passing
static analysis is NOT sufficient — the build must compile cleanly.

Do NOT fail for things that require a running environment:
- Cross-browser compatibility (needs a live browser)
- Responsive layout pixel-perfect rendering (needs a device)
- Contact form submissions or backend connectivity (needs a running server)
- Missing CI secrets (configured separately by infra)
- Workflow run results (workflows use manual trigger, that is intentional)

DO check:
1. Build passes — run build_and_test; fail immediately if it returns success=False
2. Correctness — code satisfies the stated requirements
3. Security — no hardcoded secrets/tokens; least privilege where relevant
4. Best practices — idiomatic, maintainable, following ecosystem conventions
5. Completeness — required files present, no TODOs that block the build

CRITICAL — file structure rules:
- Next.js App Router projects use `app/` at the root, NOT `src/app/`. Never fail for missing `src/` prefixed paths.
- Always use github_get_file to verify the actual file structure before drawing conclusions. If a file is not at the expected path, check the root `app/` directory.
- Do NOT assume a `src/` layout. The standard Next.js App Router layout is: `app/`, `components/`, `lib/`, `public/` — all at the repo root.

After reading files respond with a JSON object (no prose, no markdown fences):
{
  "verdict": "pass" | "fail",
  "score": 0-10,
  "issues": ["<specific, statically verifiable issue>", ...],
  "recommendations": ["<recommendation>", ...],
  "summary": "<one paragraph>"
}
"""

_QA_TOOLS = {"github_list_repos", "github_get_file", "github_create_issue", "build_and_test"}


class QAAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(
            agent_name="qa",
            model=os.getenv("QA_MODEL", "devstral-latest"),
            system_prompt=SYSTEM_PROMPT,
        )
        for schema in GITHUB_TOOL_SCHEMAS:
            if schema["name"] in _QA_TOOLS:
                self.register_tool(schema["name"], GITHUB_TOOL_FNS[schema["name"]], schema)

    async def handle_event(self, event: dict) -> None:
        await self._validate(event)

    async def _validate(self, event: dict) -> None:
        task_id = event["task_id"]
        assignment_id = event["assignment_id"]
        payload = event.get("payload", {})
        description = payload.get("description", "")
        assignment = payload.get("assignment", "")
        task_plan_id = payload.get("task_plan_id", "unknown")
        # PM sets this when dispatching validate tasks so QA knows which implement task to re-trigger
        impl_task_id = payload.get("impl_task_id", "implement_1")

        # Read accumulated retry count from whiteboard (survives re-dispatch)
        retry_key = f"qa_retry_{task_plan_id}"
        wb = await self.read_whiteboard(assignment_id)
        retry = int(wb.get(retry_key, 0))

        self.logger.info("Validating [%s] retry=%d impl_task=%s", task_id, retry, impl_task_id)
        await self.write_whiteboard(assignment_id, f"task_{task_plan_id}_status", "validating")

        whiteboard = await self.read_whiteboard(assignment_id)
        implementation = whiteboard.get("implementation", "")
        github_repo = whiteboard.get("github_repo", "")
        github_branch = whiteboard.get("github_branch", "")

        if not implementation:
            clarification = await self.ask(
                task_id,
                "engineer",
                "I'm ready to validate but can't find your implementation on the whiteboard. Can you confirm it's been written?",
                round_num=1,
            )
            whiteboard = await self.read_whiteboard(assignment_id)
            implementation = whiteboard.get("implementation", clarification or "")

        repo_hint = ""
        if github_repo:
            repo_name = github_repo.rstrip("/").split("/")[-1]
            branch_hint = f" on branch '{github_branch}'" if github_branch else ""
            repo_hint = f"\n\nGitHub repo: {github_repo} (name: {repo_name}{branch_hint}). Use github_get_file to read key source files."

        raw = await self.call_mistral(
            [
                {
                    "role": "user",
                    "content": (
                        f"Original assignment: {assignment}\n\n"
                        f"Validation task: {description}\n\n"
                        f"Implementation summary:\n{implementation[:4000]}"
                        f"{repo_hint}\n\n"
                        "Respond with JSON only."
                    ),
                }
            ]
        )

        result = _parse_verdict(raw)
        verdict = result.get("verdict", "fail")
        score = result.get("score", 0)

        await self.write_whiteboard(assignment_id, "qa_report", json.dumps(result, indent=2))
        await self.write_whiteboard(assignment_id, "qa_verdict", verdict)
        self.logger.info("QA verdict: %s (score=%s)", verdict, score)

        if verdict == "pass":
            await self.write_whiteboard(assignment_id, f"task_{task_plan_id}_status", "completed")
            await self.write_whiteboard(assignment_id, f"task_{task_plan_id}_completed_at", _now())
            await self.emit_event(
                "project_manager",
                {
                    "task_id": task_id,
                    "assignment_id": assignment_id,
                    "type": "task_complete",
                    "assigned_to": "project_manager",
                    "payload": {
                        "task_plan_id": task_plan_id,
                        "assignment": assignment,
                        "qa_score": score,
                    },
                },
            )
        else:
            issues = "; ".join(result.get("issues", ["unspecified"]))
            new_retry = retry + 1
            await self.write_whiteboard(assignment_id, retry_key, str(new_retry))
            await self.write_whiteboard(assignment_id, f"task_{task_plan_id}_status", f"qa_failed({new_retry}): {issues[:120]}")
            await self.emit_event(
                "project_manager",
                {
                    "task_id": task_id,
                    "assignment_id": assignment_id,
                    "type": "task_failed",
                    "assigned_to": "project_manager",
                    "payload": {
                        "task_plan_id": impl_task_id,
                        "assignment": assignment,
                        "reason": issues,
                        "retry_count": new_retry,
                        "qa_report": result,
                    },
                },
            )


def _parse_verdict(raw: str) -> dict:
    try:
        text = raw.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text.strip())
    except (json.JSONDecodeError, IndexError):
        return {
            "verdict": "fail",
            "score": 0,
            "issues": ["Could not parse QA response"],
            "recommendations": [],
            "summary": raw[:500],
        }


if __name__ == "__main__":
    asyncio.run(QAAgent().run())
