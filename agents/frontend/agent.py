"""
Frontend agent — devstral-latest

Next.js 16.2 specialist. Builds complete, production-ready frontend projects:
pages, components, styles, animations, and Vercel deployment config.
Pushes all files to GitHub and opens a PR.

Incoming event types: implement, research, validate, deploy (all routed here)
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, "/app")
from base_agent import BaseAgent, _now  # noqa: E402
from tools import GITHUB_TOOL_FNS, GITHUB_TOOL_SCHEMAS  # noqa: E402
from tools.github_tools import github_get_file  # noqa: E402

_HANDBOOK_REPO = "design-handbook"
_HANDBOOK_ORG = "Skogum-R-D"
_HANDBOOK_FILES = [
    "docs/stack.md",
    "docs/gotchas.md",
    "docs/design/glassmorphism.md",
    "docs/design/animations.md",
    "docs/components/button.md",
    "docs/components/card.md",
]

_STARTER_REPO = "skogum-nextjs-starter"
_STARTER_FILES = [
    "components/ui/button.tsx",
    "components/ui/card.tsx",
    "components/ui/input.tsx",
    "components/ui/badge.tsx",
    "lib/utils.ts",
    "app/globals.css",
    "app/layout.tsx",
    "package.json",
    "next.config.js",
    "tailwind.config.ts",
    "tsconfig.json",
    "postcss.config.js",
]

SYSTEM_PROMPT = """\
You are the Senior Frontend Engineer at Skogum R&D — a Next.js 16.2 expert with
a sharp eye for cutting-edge UI/UX design.

Tech stack you always use (EXACT versions — do not deviate):
- Next.js 16.2.x with App Router and TypeScript
- Tailwind CSS v3 (^3.4.0) — NOT v4
- Framer Motion ^11.3.28
- lucide-react ^0.468.0 (NOT older versions — React 19 requires this)
- React ^19.0.0 (use "^19.0.0" string — NOT RC versions)
- next/font for typography (Geist or Inter)

Project structure you generate:
  package.json          (next 16.2.x, react ^19.0.0, tailwindcss ^3.4.0, framer-motion ^11.3.28)
  next.config.ts
  tailwind.config.ts    (Tailwind v3 config — use module.exports or "export default" with type Config)
  tsconfig.json
  postcss.config.mjs    (MUST be: { plugins: { tailwindcss: {}, autoprefixer: {} } })
  app/
    layout.tsx          (root layout; set className="dark" on <html>; NO next-themes import)
    page.tsx            (home page)
    globals.css         (Tailwind v3 directives: @tailwind base/components/utilities)
  components/           (reusable UI components)
  public/               (static assets)

Do NOT generate any GitHub Actions workflows or CI/CD config. No .github/ directory.
Do NOT use next-themes — it is incompatible with React 19. Set className="dark" on <html> directly.

STARTER TEMPLATE — always use Skogum-R-D/skogum-nextjs-starter:
- Copy components/ui/button.tsx, card.tsx, input.tsx, badge.tsx verbatim — do NOT rewrite them
- Copy package.json, next.config.js, tailwind.config.ts, tsconfig.json, postcss.config.js verbatim
- The starter components already have the correct Framer Motion patterns — never modify them
- The starter files will be provided to you in context below — use them exactly as written

IMPORTANT — avoid redundant file pushes:
If the task context says "Existing GitHub repo" and "Existing feature branch", the repo already has
files from previous tasks. Do NOT re-push starter template files (button.tsx, card.tsx, input.tsx,
badge.tsx, package.json, globals.css, etc.) unless your task description explicitly asks you to set
up the project. Focus only on the files your current task specifically requires.
Push new/changed files → call build_and_test → open PR. That's it.

Critical rules — these caused production bugs and must be followed exactly:
1. Every component that uses framer-motion `motion.*` or React hooks MUST start with "use client";
2. All forwardRef components: default className="" not undefined — never concatenate className + undefined
3. Tailwind v3 postcss.config.mjs: { plugins: { tailwindcss: {}, autoprefixer: {} } }
   NOT @tailwindcss/postcss (that is v4-only)
4. CSS variable colors in globals.css use HSL WITHOUT the hsl() wrapper:
   --background: 222 47% 5%;   ← correct (Tailwind reads these as raw values)
   NOT: --background: hsl(222, 47%, 5%);
5. Dark theme --foreground must be near-WHITE (e.g., 210 40% 98%), not near-black
6. Import paths must use @/ alias (e.g., "@/components/hero"), never absolute paths
7. File names and imports must match case exactly — macOS hides case errors that break Linux/Vercel

Design principles:
- Dark theme by default: set class="dark" on <html>, never rely on system preference
- Glassmorphism cards: backdrop-blur, semi-transparent backgrounds (rgba), subtle borders
- Gradient accents: use CSS custom properties for brand colors
- Micro-animations: entrance animations with Framer Motion (fadeIn, slideUp, stagger)
- Mobile-first responsive layout
- No placeholder images — use CSS gradients or SVG illustrations instead

GitHub workflow:
1. If a repo is provided in context, use it — do NOT create another.
2. If no repo exists, create one (github_create_repo).
3. Create a feature branch if not already present (github_create_branch).
4. Push EVERY file with github_create_or_update_file — including package.json,
   tsconfig.json, tailwind.config.ts, next.config.ts, all components and pages.
5. Open a PR once all files are pushed (github_create_pr).
6. Report the PR URL in your final response.

Write complete, working code. Never use placeholders like TODO or YOUR_VALUE.
The code must run with `npm install && npm run dev` without any modifications.

MANDATORY: After editing any source file, call build_and_test before pushing or
reporting task_complete. If it returns success=False, read the error output, fix
the file, and call build_and_test again. Only push when it returns success=True.
"""

_FRONTEND_TOOLS = {
    "build_and_test",
    "github_create_repo",
    "github_create_branch",
    "github_create_or_update_file",
    "github_get_file",
    "github_list_repos",
    "github_create_pr",
}


class FrontendAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(
            agent_name="frontend",
            model=os.getenv("FRONTEND_MODEL", "devstral-latest"),
            system_prompt=SYSTEM_PROMPT,
        )
        for schema in GITHUB_TOOL_SCHEMAS:
            if schema["name"] in _FRONTEND_TOOLS:
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

    async def _fetch_handbook(self) -> str:
        """Fetch key design-handbook files and return them as a formatted context block."""
        sections: list[str] = []
        for path in _HANDBOOK_FILES:
            try:
                result = await asyncio.to_thread(
                    github_get_file, _HANDBOOK_REPO, path, "main", _HANDBOOK_ORG
                )
                sections.append(f"### {path}\n{result['content']}")
            except Exception as exc:
                self.logger.warning("Could not fetch handbook %s: %s", path, exc)
        if not sections:
            return ""
        return "## Design Handbook (read carefully — follow exactly)\n\n" + "\n\n---\n\n".join(sections)

    async def _fetch_starter(self) -> str:
        """Fetch canonical component files from skogum-nextjs-starter."""
        sections: list[str] = []
        for path in _STARTER_FILES:
            try:
                result = await asyncio.to_thread(
                    github_get_file, _STARTER_REPO, path, "main", _HANDBOOK_ORG
                )
                sections.append(f"### {path}\n```\n{result['content']}\n```")
            except Exception as exc:
                self.logger.warning("Could not fetch starter %s: %s", path, exc)
        if not sections:
            return ""
        return "## Starter Template — copy these files verbatim\n\n" + "\n\n".join(sections)

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
        self.logger.info("Frontend task [%s] retry=%d: %.120s", task_id, retry, description)
        await self.write_whiteboard(assignment_id, f"task_{task_plan_id}_status", "in_progress")

        whiteboard = await self.read_whiteboard(assignment_id)
        github_repo = whiteboard.get("github_repo", "")
        github_branch = whiteboard.get("github_branch", "")

        repo_context = ""
        if github_repo:
            repo_context = f"Existing GitHub repo: {github_repo} — do NOT create another.\n"
        if github_branch:
            repo_context += f"Existing feature branch: {github_branch} — push all files to this branch.\n"
        if repo_context:
            repo_context += "\n"

        handbook, starter = await asyncio.gather(
            self._fetch_handbook(),
            self._fetch_starter(),
        )

        result = await self.call_mistral(
            [
                {
                    "role": "user",
                    "content": (
                        f"Assignment: {assignment}\n\n"
                        f"Task: {description}\n\n"
                        f"{repo_context}"
                        f"{starter}\n\n"
                        f"{handbook}\n\n"
                        "Produce the complete implementation. Push all files to GitHub."
                    ),
                }
            ]
        )

        await self.write_whiteboard(assignment_id, "implementation", result)

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
                        "reason": "build_and_test never returned success=True — build errors not resolved",
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
                "payload": {"task_plan_id": task_plan_id, "assignment": assignment},
            },
        )

    async def handle_discussion(self, message: dict) -> None:
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
    asyncio.run(FrontendAgent().run())
