import asyncio
import json
import logging
import os
from contextlib import AsyncExitStack
from datetime import datetime, timezone
from typing import Any

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    _MCP_AVAILABLE = True
except ImportError:
    _MCP_AVAILABLE = False

import valkey.asyncio as aio_valkey
from mistralai.client import Mistral

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)


class BaseAgent:
    MAX_DISCUSSION_ROUNDS = 5

    def __init__(self, agent_name: str, model: str, system_prompt: str) -> None:
        self.agent_name = agent_name
        self.model = os.getenv(f"{agent_name.upper()}_MODEL", model)
        self.system_prompt = system_prompt
        self.logger = logging.getLogger(agent_name)

        valkey_url = os.getenv("VALKEY_URL", "valkey://localhost:6379")
        self.valkey = aio_valkey.from_url(valkey_url, decode_responses=True)
        # Separate client for pubsub — no socket_timeout so the blocking listen() never times out
        self._pubsub_client = aio_valkey.from_url(
            valkey_url,
            decode_responses=True,
            socket_timeout=None,
            socket_keepalive=True,
        )

        # 3-minute timeout — devstral responses for large codegen tasks can be slow
        self.mistral = Mistral(api_key=os.getenv("MISTRAL_API_KEY"), timeout_ms=180_000)

        # Keyed by "{task_id}:{from_agent}" — used to route discussion replies
        self._discussion_queues: dict[str, asyncio.Queue] = {}

        # Tool registry — populated by register_tool() in subclass __init__
        self._tool_fns: dict[str, callable] = {}
        self._tool_schemas: list = []  # mistralai.client.models.Tool objects
        self.MAX_TOOL_ITERATIONS = 10  # override in subclasses that need more headroom

        # MCP session lifecycle — AsyncExitStacks keep stdio connections alive
        self._mcp_exit_stacks: list[AsyncExitStack] = []

    # ── Event bus ──────────────────────────────────────────────────────────────

    TASK_TIMEOUT = int(os.getenv("TASK_TIMEOUT", "600"))  # 10 minutes

    async def _timed_handle_event(self, event: dict) -> None:
        """Wrap handle_event with a timeout; emit task_failed to PM on expiry."""
        try:
            await asyncio.wait_for(self.handle_event(event), timeout=self.TASK_TIMEOUT)
        except asyncio.TimeoutError:
            assignment_id = event.get("assignment_id", "")
            task_id = event.get("task_id", "")
            task_plan_id = event.get("payload", {}).get("task_plan_id", task_id)
            self.logger.error(
                "Task timed out after %ds: %s / %s", self.TASK_TIMEOUT, assignment_id, task_id
            )
            if assignment_id:
                await self.write_whiteboard(
                    assignment_id, f"task_{task_plan_id}_status", f"failed: timed out after {self.TASK_TIMEOUT}s"
                )
                await self.emit_event(
                    "project_manager",
                    {
                        "task_id": task_id,
                        "assignment_id": assignment_id,
                        "type": "task_failed",
                        "assigned_to": "project_manager",
                        "payload": {
                            "task_plan_id": task_plan_id,
                            "reason": f"timed out after {self.TASK_TIMEOUT}s",
                            "retry_count": event.get("payload", {}).get("retry_count", 0),
                        },
                    },
                )

    async def listen_events(self) -> None:
        """Blocking loop: pops tasks from queue:{agent_name} via BRPOP."""
        queue_key = f"queue:{self.agent_name}"
        self.logger.info(f"Listening for events on {queue_key}")
        while True:
            try:
                result = await self.valkey.brpop(queue_key, timeout=1)
                if result:
                    _, raw = result
                    event: dict = json.loads(raw)
                    self.logger.info(
                        "Event received | task_id=%s type=%s",
                        event.get("task_id"),
                        event.get("type"),
                    )
                    await self._timed_handle_event(event)
            except Exception:
                self.logger.exception("Error in listen_events")
                await asyncio.sleep(1)

    async def emit_event(self, agent_name: str, payload: dict) -> None:
        """Push a JSON task event to queue:{agent_name} via LPUSH."""
        payload.setdefault("timestamp", _now())
        await self.valkey.lpush(f"queue:{agent_name}", json.dumps(payload))
        self.logger.info(
            "→ %s | task_id=%s type=%s",
            agent_name,
            payload.get("task_id"),
            payload.get("type"),
        )

    # ── Whiteboard ─────────────────────────────────────────────────────────────

    async def read_whiteboard(self, task_id: str) -> dict[str, str]:
        """Return all fields from whiteboard:{task_id} hash."""
        return await self.valkey.hgetall(f"whiteboard:{task_id}")

    async def write_whiteboard(self, task_id: str, key: str, value: str) -> None:
        """Set a field on whiteboard:{task_id} hash."""
        await self.valkey.hset(f"whiteboard:{task_id}", key, value)
        preview = (value[:80] + "…") if len(value) > 80 else value
        self.logger.debug("Whiteboard [%s] %s = %s", task_id, key, preview)

    # ── Discussion channels ────────────────────────────────────────────────────

    async def listen_discussions(self) -> None:
        """Blocking loop: receives pub/sub messages from discussion:* channels.
        Reconnects automatically on any connection error."""
        while True:
            try:
                pubsub = self._pubsub_client.pubsub()
                await pubsub.psubscribe("discussion:*")
                self.logger.info("Subscribed to discussion:*")
                async for message in pubsub.listen():
                    if message["type"] != "pmessage":
                        continue
                    try:
                        data: dict = json.loads(message["data"])
                        if data.get("to") != self.agent_name:
                            continue
                        from_agent = data["from"]
                        task_id = data["task_id"]
                        queue_key = f"{task_id}:{from_agent}"
                        if queue_key in self._discussion_queues:
                            # Route back to a waiting ask() call
                            await self._discussion_queues[queue_key].put(data)
                        else:
                            await self.handle_discussion(data)
                    except Exception:
                        self.logger.exception("Error handling discussion message")
            except Exception:
                self.logger.warning("Discussion loop disconnected, reconnecting in 2s...")
                await asyncio.sleep(2)

    async def publish_discussion(
        self, task_id: str, to: str, message: str, round_num: int
    ) -> None:
        """Publish a message to discussion:{task_id}."""
        payload = {
            "task_id": task_id,
            "from": self.agent_name,
            "to": to,
            "round": round_num,
            "message": message,
            "timestamp": _now(),
        }
        await self.valkey.publish(f"discussion:{task_id}", json.dumps(payload))
        self.logger.info(
            "Discussion → %s | task_id=%s round=%d", to, task_id, round_num
        )

    async def ask(
        self,
        task_id: str,
        with_agent: str,
        question: str,
        round_num: int,
        timeout: float = 60.0,
    ) -> str | None:
        """
        Publish a discussion message and block until the recipient replies.

        Works because listen_discussions() runs concurrently in asyncio.gather —
        it puts the reply into a queue that ask() awaits here.
        Returns None on timeout.
        """
        if round_num > self.MAX_DISCUSSION_ROUNDS:
            self.logger.warning(
                "Max discussion rounds (%d) reached, skipping ask()",
                self.MAX_DISCUSSION_ROUNDS,
            )
            return None

        queue_key = f"{task_id}:{with_agent}"
        self._discussion_queues[queue_key] = asyncio.Queue()
        await self.publish_discussion(task_id, with_agent, question, round_num)
        try:
            reply = await asyncio.wait_for(
                self._discussion_queues[queue_key].get(), timeout=timeout
            )
            return reply.get("message")
        except asyncio.TimeoutError:
            self.logger.warning("ask() timed out waiting for %s", with_agent)
            return None
        finally:
            self._discussion_queues.pop(queue_key, None)

    # ── Tools ──────────────────────────────────────────────────────────────────

    def register_tool(self, name: str, fn: callable, schema: dict) -> None:
        """Register a callable tool and its Mistral function schema.

        schema is the inner function dict: {name, description, parameters}.
        The Tool wrapper is constructed here so callers stay SDK-free.
        """
        from mistralai.client.models import Function, Tool

        self._tool_fns[name] = fn
        self._tool_schemas.append(
            Tool(
                function=Function(
                    name=schema["name"],
                    description=schema.get("description", ""),
                    parameters=schema["parameters"],
                )
            )
        )
        self.logger.debug("Registered tool: %s", name)

    async def connect_mcp_server(
        self,
        command: str,
        args: list[str],
        env: dict[str, str] | None = None,
        prefix: str = "",
    ) -> None:
        """Spawn an MCP server process, fetch its tools, and register them.

        Tools are available to the Mistral tool loop immediately after this call.
        The connection stays open for the lifetime of the agent; all sessions are
        closed automatically when run() exits.

        Args:
            command: Executable to run (e.g. "npx", "python", "uvx").
            args:    Arguments to the executable (e.g. ["-y", "@modelcontextprotocol/server-github"]).
            env:     Extra environment variables for the server process.
            prefix:  Prepended to every tool name as "{prefix}__{tool_name}" to
                     avoid collisions when multiple MCP servers are connected.
        """
        if not _MCP_AVAILABLE:
            self.logger.warning("mcp package not installed — cannot connect MCP server: %s", command)
            return

        merged_env = {**os.environ, **(env or {})}
        server_params = StdioServerParameters(command=command, args=args, env=merged_env)

        stack = AsyncExitStack()
        try:
            read, write = await stack.enter_async_context(stdio_client(server_params))
            session: ClientSession = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
        except Exception as exc:
            await stack.aclose()
            self.logger.error("Failed to connect MCP server %s: %s", command, exc)
            return

        tools_result = await session.list_tools()
        registered = []
        for tool in tools_result.tools:
            tool_name = f"{prefix}__{tool.name}" if prefix else tool.name
            schema = {
                "name": tool_name,
                "description": tool.description or "",
                "parameters": tool.inputSchema or {"type": "object", "properties": {}},
            }

            def _make_caller(orig_name: str, sess: "ClientSession"):
                async def caller(**kwargs):
                    result = await sess.call_tool(orig_name, kwargs)
                    parts = []
                    for item in result.content:
                        if hasattr(item, "text"):
                            parts.append(item.text)
                        else:
                            parts.append(str(item))
                    return {"result": "\n".join(parts)}
                return caller

            self.register_tool(tool_name, _make_caller(tool.name, session), schema)
            registered.append(tool_name)

        self._mcp_exit_stacks.append(stack)
        self.logger.info(
            "MCP server connected: %s %s → %d tools: %s",
            command, " ".join(args[:2]), len(registered), registered,
        )

    async def _pre_tool_args_hook(self, name: str, args: dict) -> dict:
        """Override in subclasses to modify tool arguments before execution."""
        return args

    async def _execute_tool(self, tool_call) -> str:
        """Execute one tool call; return a JSON string result."""
        name = tool_call.function.name
        args = tool_call.function.arguments
        if isinstance(args, str):
            args = json.loads(args)

        args = await self._pre_tool_args_hook(name, args)

        fn = self._tool_fns.get(name)
        if fn is None:
            self.logger.error("Unknown tool: %s", name)
            result: Any = {"error": f"Unknown tool: {name}"}
        else:
            self.logger.info("Tool call: %s(%s)", name, str(args)[:120])
            try:
                # MCP callers are coroutines; native tools are blocking — handle both
                if asyncio.iscoroutinefunction(fn):
                    raw = await fn(**args)
                else:
                    raw = await asyncio.to_thread(fn, **args)
                result = raw if isinstance(raw, (dict, list)) else {"result": raw}
            except Exception as exc:
                self.logger.exception("Tool %s raised: %s", name, exc)
                result = {"error": str(exc)}

        out = json.dumps(result, ensure_ascii=False)
        self.logger.info("Tool result: %s → %.200s", name, out)
        return out

    # ── Mistral ────────────────────────────────────────────────────────────────

    async def call_mistral(self, messages: list[dict]) -> str:
        """Agentic Mistral call with optional tool loop.

        If tools are registered, runs a tool-call loop (max 10 iterations)
        until the model returns a text response with no further tool calls.
        Falls back to a plain completion when no tools are registered.
        """
        from mistralai.client.models import ToolMessage

        MAX_ITERATIONS = self.MAX_TOOL_ITERATIONS
        tools_arg = self._tool_schemas if self._tool_schemas else None
        history: list = [{"role": "system", "content": self.system_prompt}] + list(messages)

        for iteration in range(MAX_ITERATIONS):
            response = await asyncio.to_thread(
                self.mistral.chat.complete,
                model=self.model,
                messages=history,
                tools=tools_arg,
                timeout_ms=180_000,
            )
            choice = response.choices[0]

            if not choice.message.tool_calls:
                return choice.message.content or ""

            self.logger.info(
                "Tool loop iteration %d/%d: %d call(s)",
                iteration + 1, MAX_ITERATIONS, len(choice.message.tool_calls),
            )

            # Append the assistant message (SDK object — Pydantic coerces mixed lists)
            history.append(choice.message)

            # Execute all tool calls and collect results
            for tc in choice.message.tool_calls:
                result_str = await self._execute_tool(tc)
                history.append(
                    ToolMessage(
                        tool_call_id=tc.id,
                        name=tc.function.name,
                        content=result_str,
                    )
                )

        # Safety cap: force a final text response after max iterations
        self.logger.warning("Reached max tool iterations (%d), forcing final response", self.MAX_TOOL_ITERATIONS)
        response = await asyncio.to_thread(
            self.mistral.chat.complete,
            model=self.model,
            messages=history,
            tools=tools_arg,
            timeout_ms=180_000,
        )
        return response.choices[0].message.content or ""

    # ── Hooks (override in subclasses) ─────────────────────────────────────────

    async def startup(self) -> None:
        """Called once before the event loop starts.

        Reads MCP_SERVERS from the environment and connects each server.
        Override in subclasses for hardcoded servers, but call super().startup()
        first so the env-driven servers are always loaded:

            async def startup(self):
                await super().startup()
                await self.connect_mcp_server(command="uvx", args=["my-server"], prefix="x")

        MCP_SERVERS format (JSON array in the environment variable):

            MCP_SERVERS=[
              {"prefix": "fetch", "command": "uvx", "args": ["mcp-server-fetch"]},
              {"prefix": "gh",    "command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"],
               "env": {"GITHUB_TOKEN": "..."}}
            ]
        """
        raw = os.getenv("MCP_SERVERS", "").strip()
        if not raw:
            return
        try:
            configs = json.loads(raw)
        except json.JSONDecodeError as exc:
            self.logger.error("MCP_SERVERS is not valid JSON — skipping: %s", exc)
            return
        for cfg in configs:
            await self.connect_mcp_server(
                command=cfg["command"],
                args=cfg.get("args", []),
                env=cfg.get("env"),
                prefix=cfg.get("prefix", ""),
            )

    async def handle_event(self, event: dict) -> None:
        self.logger.warning("Unhandled event type: %s", event.get("type"))

    async def handle_discussion(self, message: dict) -> None:
        self.logger.debug(
            "Unhandled discussion from %s round=%s", message.get("from"), message.get("round")
        )

    # ── Entry point ────────────────────────────────────────────────────────────

    async def run(self) -> None:
        self.logger.info("Agent %r starting (model=%s)", self.agent_name, self.model)
        await self.startup()
        try:
            await asyncio.gather(
                self.listen_events(),
                self.listen_discussions(),
            )
        finally:
            for stack in reversed(self._mcp_exit_stacks):
                try:
                    await stack.aclose()
                except Exception:
                    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
