"""
Submit an assignment to the Skogum AI Consulting system.

Usage:
    python scripts/submit_assignment.py "Your assignment description"
    VALKEY_URL=valkey://myhost:6379 python scripts/submit_assignment.py "..."
"""

import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone

import valkey.asyncio as aio_valkey


async def submit(description: str) -> None:
    url = os.getenv("VALKEY_URL", "valkey://localhost:6379")
    client = aio_valkey.from_url(url, decode_responses=True)

    assignment_id = str(uuid.uuid4())
    event = {
        "task_id": assignment_id,
        "type": "assignment",
        "assigned_to": "project_manager",
        "payload": {"description": description},
        "status": "pending",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    await client.lpush("queue:project_manager", json.dumps(event))
    print(f"Assignment submitted — id={assignment_id}")
    print(f"Description: {description[:120]}{'...' if len(description) > 120 else ''}")
    print()
    print("Monitor whiteboard:")
    print(f"  valkey-cli hgetall whiteboard:{assignment_id}")
    print()
    print("Follow logs:")
    print("  docker-compose logs -f")

    await client.aclose()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/submit_assignment.py \"Your assignment description\"")
        sys.exit(1)
    asyncio.run(submit(sys.argv[1]))
