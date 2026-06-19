"""Natural language interface layer using Claude's tool use API.

Handles the conversation loop: user input -> Claude -> tool dispatch -> result
fed back to Claude -> natural language response to user.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime

import anthropic

from labquery.lims_client import LIMSClient
from labquery.plr_runner import PLRRunner
from labquery.tools import SYSTEM_PROMPT, TOOLS


@dataclass
class Message:
    role: str
    content: str | list


@dataclass
class Conversation:
    messages: list[Message] = field(default_factory=list)

    def add_user(self, text: str) -> None:
        self.messages.append(Message(role="user", content=text))

    def add_assistant(self, content: str | list) -> None:
        self.messages.append(Message(role="assistant", content=content))

    def add_tool_result(self, tool_use_id: str, result: str) -> None:
        self.messages.append(
            Message(
                role="user",
                content=[
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": result,
                    }
                ],
            )
        )

    def to_api_messages(self) -> list[dict]:
        return [{"role": m.role, "content": m.content} for m in self.messages]


class NLLayer:
    """Orchestrates the Claude tool-use loop for labquery."""

    def __init__(
        self,
        lims: LIMSClient,
        plr: PLRRunner,
        model: str = "claude-sonnet-4-6",
    ):
        self.lims = lims
        self.plr = plr
        self.model = model
        self.client = anthropic.Anthropic()
        self.conversation = Conversation()

    def query(self, user_input: str) -> str:
        """Process a user query through the full tool-use loop.

        Returns the final natural language response.
        """
        self.conversation.add_user(user_input)

        while True:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=self.conversation.to_api_messages(),
            )

            if response.stop_reason == "tool_use":
                self.conversation.add_assistant(
                    [block.model_dump() for block in response.content]
                )
                for block in response.content:
                    if block.type == "tool_use":
                        result = self._dispatch(block.name, block.input)
                        self.conversation.add_tool_result(block.id, result)
            else:
                text = self._extract_text(response)
                self.conversation.add_assistant(text)
                return text

    def _dispatch(self, tool_name: str, tool_input: dict) -> str:
        """Route a tool call to the appropriate handler and return the result as a string."""
        handlers = {
            "query_sample_status": self._handle_query_sample,
            "check_inventory": self._handle_check_inventory,
            "run_protocol": self._handle_run_protocol,
            "get_run_history": self._handle_get_run_history,
        }

        handler = handlers.get(tool_name)
        if not handler:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})

        try:
            return handler(tool_input)
        except Exception as e:
            return json.dumps({"error": str(e)})

    def _handle_query_sample(self, inp: dict) -> str:
        sample = self.lims.get_sample(inp["sample_id"])
        if sample is None:
            return json.dumps({"error": f"Sample {inp['sample_id']} not found"})
        return json.dumps({
            "sample_id": sample.sample_id,
            "sample_type": sample.sample_type,
            "location_rack": sample.location_rack,
            "location_position": sample.location_position,
            "volume_ul": sample.volume_ul,
            "status": sample.status,
            "last_modified": sample.last_modified.isoformat() if sample.last_modified else None,
        })

    def _handle_check_inventory(self, inp: dict) -> str:
        samples = self.lims.list_samples(
            sample_type=inp["sample_type"],
            min_volume_ul=inp.get("min_volume_ul", 50),
        )
        required = inp.get("required_count")
        result = {
            "sample_type": inp["sample_type"],
            "available_count": len(samples),
            "total_volume_ul": sum(s.volume_ul for s in samples),
        }
        if required is not None:
            result["required_count"] = required
            result["sufficient"] = len(samples) >= required
            result["shortfall"] = max(0, required - len(samples))
        return json.dumps(result)

    def _handle_run_protocol(self, inp: dict) -> str:
        samples = []
        missing = []
        for sid in inp["sample_ids"]:
            s = self.lims.get_sample(sid)
            if s is None:
                missing.append(sid)
            else:
                samples.append(s)

        if missing:
            return json.dumps({
                "error": f"Samples not found: {', '.join(missing)}",
            })

        result = self.plr.run_protocol(
            protocol_name=inp["protocol_name"],
            samples=samples,
        )

        for sid, consumed_ul in result.volumes_consumed.items():
            s = self.lims.get_sample(sid)
            if s:
                self.lims.update_sample(sid, volume_ul=s.volume_ul - consumed_ul)

        return json.dumps({
            "run_id": result.run_id,
            "status": result.status,
            "samples_processed": len(samples),
            "estimated_minutes": result.estimated_minutes,
            "volumes_consumed": result.volumes_consumed,
        })

    def _handle_get_run_history(self, inp: dict) -> str:
        runs = self.lims.get_run_history(
            inp["sample_id"], days_back=inp.get("days_back", 7)
        )
        return json.dumps([
            {
                "run_id": r.run_id,
                "protocol_name": r.protocol_name,
                "started_at": r.started_at.isoformat(),
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "status": r.status,
            }
            for r in runs
        ])

    @staticmethod
    def _extract_text(response) -> str:
        for block in response.content:
            if hasattr(block, "text"):
                return block.text
        return ""
