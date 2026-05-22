"""
Engineer agent — devstral-latest

Implements the technical solution: writes code, CI/CD pipelines, Dockerfiles,
and Kubernetes manifests. Consults the researcher via discussion if needed,
then hands off to QA.
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, "/app")
from base_agent import BaseAgent, _now  # noqa: E402
from tools import GITHUB_TOOL_FNS, GITHUB_TOOL_SCHEMAS  # noqa: E402

SYSTEM_PROMPT = """\
You are the Senior DevOps Engineer at Skogum AI Consulting. You write clean,
production-ready code and infrastructure-as-code.

You have access to GitHub tools. Use them to actually create repos, push files,
and open PRs — don't just describe what should be done.

Workflow for implementation tasks:
1. If a GitHub repo is already provided in the task context, USE THAT REPO — do NOT create another one.
2. If no repo exists yet, create one (github_create_repo).
3. Create a feature branch if not already present (github_create_branch).
4. Push each file using github_create_or_update_file.
5. Open a PR once all files are pushed (github_create_pr).
6. Report the PR URL in your final response.

Your code must be:
- Specific and complete (no placeholders like <YOUR_VALUE> without explanation)
- Following current best practices for security and maintainability

Use the researcher's findings from the whiteboard as your primary reference.

For any Next.js project, copy UI components from Skogum-R-D/skogum-nextjs-starter
instead of writing them from scratch. Use github_get_file to read button.tsx,
card.tsx, input.tsx, badge.tsx, lib/utils.ts, and config files from that repo.

MANDATORY: After editing any source file, call build_and_test before pushing or
reporting task_complete. If it returns success=False, read the error output, fix
the file, and call build_and_test again. Only push when it returns success=True.
"""


_ENGINEER_TOOLS = {
    "build_and_test",
    "github_create_repo",
    "github_create_branch",
    "github_create_or_update_file",
    "github_get_file",
    "github_list_repos",
    "github_create_pr",
}


class EngineerAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(
            agent_name="engineer",
            model=os.getenv("ENGINEER_MODEL", "devstral-latest"),
            system_prompt=SYSTEM_PROMPT,
        )
        for schema in GITHUB_TOOL_SCHEMAS:
            if schema["name"] in _ENGINEER_TOOLS:
                self.register_tool(schema["name"], GITHUB_TOOL_FNS[schema["name"]], schema)
        self._current_assignment_id: str = ""
        self._build_called: bool = False
        self._build_verified: bool = False
        self.MAX_TOOL_ITERATIONS = 30

    async def _pre_tool_args_hook(self, name: str, args: dict) -> dict:
        """Force all file pushes to the whiteboard branch — never main."""
        if name == "github_create_or_update_file" and self._current_assignment_id:
            wb = await self.read_whiteboard(self._current_assignment_id)
            branch = wb.get("github_branch", "")
            if branch:
                if args.get("branch", "main") != branch:
                    self.logger.info("Forcing branch %s → %s for %s", args.get("branch"), branch, args.get("path"))
                args["branch"] = branch
        return args

    async def _execute_tool(self, tool_call) -> str:
        result_str = await super()._execute_tool(tool_call)
        if not self._current_assignment_id:
            return result_str
        try:
            result = json.loads(result_str)
            if "error" in result:
                return result_str
            wb = await self.read_whiteboard(self._current_assignment_id)

            if tool_call.function.name == "github_create_repo":
                if not wb.get("github_repo") and result.get("html_url"):
                    await self.write_whiteboard(self._current_assignment_id, "github_repo", result["html_url"])
                    self.logger.info("Saved github_repo: %s", result["html_url"])

            elif tool_call.function.name == "github_create_branch":
                if not wb.get("github_branch") and result.get("branch"):
                    await self.write_whiteboard(self._current_assignment_id, "github_branch", result["branch"])
                    self.logger.info("Saved github_branch: %s", result["branch"])

            elif tool_call.function.name == "build_and_test":
                self._build_called = True
                if result.get("success"):
                    self._build_verified = True
                    self.logger.info("Build verified successfully")
                else:
                    self.logger.warning("Build failed: %s", str(result.get("output", ""))[:200])

        except Exception:
            pass
        return result_str

    async def handle_event(self, event: dict) -> None:
        await self._implement(event)

    async def _implement(self, event: dict) -> None:
        task_id = event["task_id"]
        assignment_id = event["assignment_id"]
        payload = event.get("payload", {})
        description = payload.get("description", "")
        assignment = payload.get("assignment", "")
        task_plan_id = payload.get("task_plan_id", "unknown")
        retry = payload.get("retry_count", 0)

        self._current_assignment_id = assignment_id
        self._build_called = False
        self._build_verified = False
        self.logger.info("Implementing [%s] retry=%d: %.120s", task_id, retry, description)
        await self.write_whiteboard(assignment_id, f"task_{task_plan_id}_status", "in_progress")

        whiteboard = await self.read_whiteboard(assignment_id)
        research = whiteboard.get("research_findings", "")
        github_repo = whiteboard.get("github_repo", "")
        github_branch = whiteboard.get("github_branch", "")

        # Optionally ask the researcher a clarifying question (round 1)
        if not research and retry == 0:
            clarification = await self.ask(
                task_id,
                "researcher",
                f"I'm about to implement: {description}. What are the key constraints I should know?",
                round_num=1,
            )
            if clarification:
                research = f"Clarification from researcher:\n{clarification}"

        repo_context = ""
        if github_repo:
            repo_context = f"Existing GitHub repo: {github_repo} — do NOT create another.\n"
        if github_branch:
            repo_context += f"Existing feature branch: {github_branch} — push all files to this branch, do NOT create another.\n"
        if repo_context:
            repo_context += "\n"

        implementation = await self.call_mistral(
            [
                {
                    "role": "user",
                    "content": (
                        f"Assignment: {assignment}\n\n"
                        f"Implementation task: {description}\n\n"
                        f"{repo_context}"
                        f"Research findings:\n{research or '(none available)'}\n\n"
                        "Produce the complete implementation."
                    ),
                }
            ]
        )

        await self.write_whiteboard(assignment_id, "implementation", implementation)

        if self._build_called and not self._build_verified:
            self.logger.error("Task %s: build_and_test was called but never passed — emitting task_failed", task_plan_id)
            await self.write_whiteboard(assignment_id, f"task_{task_plan_id}_status", "failed: build did not pass")
            await self.emit_event(
                "project_manager",
                {
                    "task_id": task_id,
                    "assignment_id": assignment_id,
                    "type": "task_failed",
                    "assigned_to": "project_manager",
                    "payload": {
                        "task_plan_id": task_plan_id,
                        "assignment": assignment,
                        "reason": "build_and_test never returned success=True — build errors were not resolved",
                        "retry_count": retry,
                    },
                },
            )
            return

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
                },
            },
        )

    async def handle_discussion(self, message: dict) -> None:
        """Answer QA questions about implementation details."""
        task_id = message["task_id"]
        from_agent = message["from"]
        question = message["message"]
        round_num = message.get("round", 1)

        if round_num > self.MAX_DISCUSSION_ROUNDS:
            return

        assignment_id = task_id.split("/")[0]
        whiteboard = await self.read_whiteboard(assignment_id)
        impl = whiteboard.get("implementation", "(not yet written)")

        answer = await self.call_mistral(
            [
                {
                    "role": "user",
                    "content": (
                        f"{from_agent} asks (round {round_num}): {question}\n\n"
                        f"My implementation:\n{impl[:2000]}\n\n"
                        "Answer concisely."
                    ),
                }
            ]
        )
        await self.publish_discussion(task_id, from_agent, answer, round_num + 1)


if __name__ == "__main__":
    asyncio.run(EngineerAgent().run())
