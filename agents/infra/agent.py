"""
Infra agent — mistral-medium-latest

Handles deployment and provisioning tasks. Reads the validated implementation
from the whiteboard, produces a deployment plan, and reports completion.
"""

import asyncio
import os
import sys

sys.path.insert(0, "/app")
from base_agent import BaseAgent, _now  # noqa: E402
from tools import GITHUB_TOOL_FNS, GITHUB_TOOL_SCHEMAS  # noqa: E402

SYSTEM_PROMPT = """\
You are the Infrastructure Lead at Skogum AI Consulting. You specialise in
Kubernetes, cloud provisioning, IaC (Terraform/Pulumi), and GitOps.

You have access to GitHub tools. Use them to actually configure the repository:
add secrets, create repos, and merge PRs when ready.

When given a deployment or infrastructure task:
1. Add required GitHub Actions secrets (github_add_secret) — KUBE_CONFIG, SLACK_WEBHOOK_URL, etc.
2. Create any required repos (github_create_repo) if they don't exist.
3. Merge the engineer's PR if QA has passed (github_merge_pr).
4. Report what was actually done, with resource URLs.
"""

_INFRA_TOOLS = {
    "github_create_repo",
    "github_create_branch",
    "github_create_or_update_file",
    "github_get_file",
    "github_list_repos",
    "github_create_pr",
    "github_merge_pr",
    "github_add_secret",
}


class InfraAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(
            agent_name="infra",
            model=os.getenv("INFRA_MODEL", "mistral-medium-latest"),
            system_prompt=SYSTEM_PROMPT,
        )
        for schema in GITHUB_TOOL_SCHEMAS:
            if schema["name"] in _INFRA_TOOLS:
                self.register_tool(schema["name"], GITHUB_TOOL_FNS[schema["name"]], schema)

    async def handle_event(self, event: dict) -> None:
        await self._deploy(event)

    async def _deploy(self, event: dict) -> None:
        task_id = event["task_id"]
        assignment_id = event["assignment_id"]
        payload = event.get("payload", {})
        description = payload.get("description", "")
        assignment = payload.get("assignment", "")
        task_plan_id = payload.get("task_plan_id", "unknown")

        self.logger.info("Planning deployment [%s]: %.120s", task_id, description)
        await self.write_whiteboard(assignment_id, f"task_{task_plan_id}_status", "in_progress")

        whiteboard = await self.read_whiteboard(assignment_id)
        implementation = whiteboard.get("implementation", "(not found)")
        qa_report = whiteboard.get("qa_report", "")

        deployment_plan = await self.call_mistral(
            [
                {
                    "role": "user",
                    "content": (
                        f"Assignment: {assignment}\n\n"
                        f"Deployment task: {description}\n\n"
                        f"Validated implementation:\n{implementation[:3000]}\n\n"
                        f"QA report:\n{qa_report[:500] or '(none)'}\n\n"
                        "Produce the complete deployment plan."
                    ),
                }
            ]
        )

        await self.write_whiteboard(assignment_id, "deployment_plan", deployment_plan)
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


if __name__ == "__main__":
    asyncio.run(InfraAgent().run())
