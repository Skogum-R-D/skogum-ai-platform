"""
Researcher agent — mistral-medium-latest

Handles research tasks: gathers best practices, documentation references, and
architectural guidance. Writes structured findings to the whiteboard, then
notifies the project manager of completion.
"""

import asyncio
import os
import sys

sys.path.insert(0, "/app")
from base_agent import BaseAgent, _now  # noqa: E402
from tools import GITHUB_TOOL_FNS, GITHUB_TOOL_SCHEMAS  # noqa: E402

SYSTEM_PROMPT = """\
You are the Senior Researcher at Skogum AI Consulting. You are thorough,
precise, and opinionated about engineering best practices.

You have read-only access to GitHub tools. Use github_list_repos to discover
existing repos and github_get_file to read existing code before making
recommendations — avoid duplicating work that already exists.

When given a research task:
1. Check existing repos for relevant patterns (github_list_repos, github_get_file).
2. Identify the key technical areas that need investigation.
3. Provide concrete, actionable findings — not generic advice.
4. Include specific tool recommendations, version constraints, and gotchas.
5. Structure your output so the engineer can act on it immediately.

Format your findings as structured Markdown with clear headings.
"""

_RESEARCHER_TOOLS = {"github_list_repos", "github_get_file"}


class ResearcherAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(
            agent_name="researcher",
            model=os.getenv("RESEARCHER_MODEL", "mistral-medium-latest"),
            system_prompt=SYSTEM_PROMPT,
        )
        for schema in GITHUB_TOOL_SCHEMAS:
            if schema["name"] in _RESEARCHER_TOOLS:
                self.register_tool(schema["name"], GITHUB_TOOL_FNS[schema["name"]], schema)

    async def handle_event(self, event: dict) -> None:
        # Accept any type — PM may use free-form type names from Mistral plan
        await self._do_research(event)

    async def _do_research(self, event: dict) -> None:
        task_id = event["task_id"]
        assignment_id = event["assignment_id"]
        payload = event.get("payload", {})
        description = payload.get("description", "")
        assignment = payload.get("assignment", "")
        task_plan_id = payload.get("task_plan_id", "unknown")

        self.logger.info("Researching [%s]: %.120s", task_id, description)
        await self.write_whiteboard(assignment_id, f"task_{task_plan_id}_status", "in_progress")

        # Read any existing whiteboard context
        whiteboard = await self.read_whiteboard(assignment_id)

        findings = await self.call_mistral(
            [
                {
                    "role": "user",
                    "content": (
                        f"Assignment: {assignment}\n\n"
                        f"Research task: {description}\n\n"
                        f"Existing whiteboard context:\n"
                        f"{_fmt_whiteboard(whiteboard)}\n\n"
                        "Provide detailed, actionable research findings."
                    ),
                }
            ]
        )

        await self.write_whiteboard(assignment_id, "research_findings", findings)
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
        """Respond to ad-hoc questions from other agents during their tasks."""
        task_id = message["task_id"]
        from_agent = message["from"]
        question = message["message"]
        round_num = message.get("round", 1)

        if round_num > self.MAX_DISCUSSION_ROUNDS:
            return

        whiteboard = await self.read_whiteboard(task_id.split("/")[0])
        answer = await self.call_mistral(
            [
                {
                    "role": "user",
                    "content": (
                        f"A colleague asks (round {round_num}): {question}\n\n"
                        f"Whiteboard context:\n{_fmt_whiteboard(whiteboard)}\n\n"
                        "Answer concisely and technically."
                    ),
                }
            ]
        )
        await self.publish_discussion(task_id, from_agent, answer, round_num + 1)


def _fmt_whiteboard(wb: dict) -> str:
    if not wb:
        return "(empty)"
    return "\n".join(f"  {k}: {v[:200]}" for k, v in wb.items())


if __name__ == "__main__":
    asyncio.run(ResearcherAgent().run())
