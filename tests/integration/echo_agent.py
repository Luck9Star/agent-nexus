#!/usr/bin/env python3
"""Minimal IPC-speaking echo subprocess for integration testing.

Reads PlatformToAgent JSON-lines from stdin, writes AgentToPlatform JSON-lines
to stdout. Handles:
- heartbeat: responds with progress "pong"
- chat: echoes content as result
- task: returns completed result
- data_reference: acknowledges with progress
- list_tools: returns a fake tool list
"""

from __future__ import annotations

import json
import sys


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            # Non-JSON input — send error
            resp = {"type": "error", "error": "Invalid JSON"}
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
            continue

        msg_type = msg.get("type", "")
        content = msg.get("content", "")

        # Heartbeat ping/pong
        if msg_type == "chat" and content == "__heartbeat__":
            resp = {"type": "progress", "content": "pong"}
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
            continue

        # Tool listing
        if msg_type == "chat" and content == "__list_tools__":
            resp = {
                "type": "result",
                "content": json.dumps(["echo", "ping", "calculate"]),
                "status": "completed",
            }
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
            continue

        # Chat echo
        if msg_type == "chat":
            resp = {
                "type": "result",
                "content": f"echo: {content}",
                "status": "completed",
                "conversation_id": msg.get("conversation_id"),
            }
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
            continue

        # Task execution
        if msg_type == "task":
            task_id = msg.get("task_id", "unknown")
            desc = msg.get("content", "")

            # Send progress first
            progress = {
                "type": "progress",
                "content": f"working on: {desc[:50]}",
                "task_id": task_id,
                "progress_pct": 50.0,
            }
            sys.stdout.write(json.dumps(progress) + "\n")
            sys.stdout.flush()

            # Then result
            result = {
                "type": "result",
                "content": f"completed: {desc}",
                "task_id": task_id,
                "status": "completed",
            }
            sys.stdout.write(json.dumps(result) + "\n")
            sys.stdout.flush()
            continue

        # Data reference acknowledgment
        if msg_type == "data_reference":
            resp = {
                "type": "progress",
                "content": f"received data ref: {msg.get('ref_id', 'unknown')}",
            }
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()

            result = {
                "type": "result",
                "content": "data reference acknowledged",
                "status": "completed",
            }
            sys.stdout.write(json.dumps(result) + "\n")
            sys.stdout.flush()
            continue

        # Unknown message type
        resp = {"type": "error", "error": f"Unknown message type: {msg_type}"}
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
