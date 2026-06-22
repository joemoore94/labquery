"""Natural language interface layer using Claude's tool use API.

Handles the conversation loop: user input -> Claude -> tool dispatch -> result
fed back to Claude -> natural language response to user.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from dataclasses import dataclass, field

import anthropic

from labquery.lims_client import LIMSClient
from labquery.measure import measure_well
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


class ToolDispatcher:
    """Routes tool calls to the appropriate LIMS/PLR handler.

    Shared by NLLayer (blocking CLI) and the WebSocket server (streaming).
    """

    def __init__(self, lims: LIMSClient, plr: PLRRunner):
        self.lims = lims
        self.plr = plr

    def dispatch(self, tool_name: str, tool_input: dict) -> str:
        handlers = {
            "query_sample_status": self._handle_query_sample,
            "check_inventory": self._handle_check_inventory,
            "run_protocol": self._handle_run_protocol,
            "list_sample_ids": self._handle_list_sample_ids,
            "measure_well": self._handle_measure_well,
            "list_protocols": self._handle_list_protocols,
            "get_deck_status": self._handle_get_deck_status,
        }

        handler = handlers.get(tool_name)
        if not handler:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})

        try:
            return handler(tool_input)
        except Exception as e:
            return json.dumps({"error": str(e)})

    async def dispatch_async(self, tool_name: str, tool_input: dict) -> str:
        """Async dispatch. Runs blocking handlers in a thread to avoid stalling the event loop."""
        if tool_name == "run_protocol" and self.plr.bridge_ready:
            try:
                return await self._handle_run_protocol_async(tool_input)
            except Exception as e:
                return json.dumps({"error": str(e)})
        return await asyncio.to_thread(self.dispatch, tool_name, tool_input)

    def _handle_query_sample(self, inp: dict) -> str:
        sample = self.lims.get_sample(inp["sample_id"])
        if sample is None:
            return json.dumps({"error": f"Sample {inp['sample_id']} not found"})
        return json.dumps({
            "sample_id": sample.sample_id,
            "material_type": sample.material_type,
            "volume_ul": sample.volume_ul,
            "concentration": sample.concentration,
            "concentration_unit": sample.concentration_unit,
            "labware_vendor": sample.labware_vendor,
            "labware_catalog": sample.labware_catalog,
            "sequence_url": sample.sequence_url,
            "created": sample.created.isoformat() if sample.created else None,
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
        samples, missing = self._resolve_samples(inp["sample_ids"])
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
                self.lims.update_sample_volume(sid, s.volume_ul - consumed_ul)

        return self._format_run_result(result, samples)

    async def _handle_run_protocol_async(self, inp: dict) -> str:
        samples, missing = self._resolve_samples(inp["sample_ids"])
        if missing:
            return json.dumps({
                "error": f"Samples not found: {', '.join(missing)}",
            })

        result = await self.plr.run_protocol_async(
            protocol_name=inp["protocol_name"],
            samples=samples,
        )

        for sid, consumed_ul in result.volumes_consumed.items():
            s = self.lims.get_sample(sid)
            if s:
                self.lims.update_sample_volume(sid, s.volume_ul - consumed_ul)

        return self._format_run_result(result, samples)

    def _resolve_samples(self, sample_ids: list[str]) -> tuple[list, list[str]]:
        samples = []
        missing = []
        for sid in sample_ids:
            s = self.lims.get_sample(sid)
            if s is None:
                missing.append(sid)
            else:
                samples.append(s)
        return samples, missing

    @staticmethod
    def _format_run_result(result, samples) -> str:
        return json.dumps({
            "run_id": result.run_id,
            "status": result.status,
            "samples_processed": len(samples),
            "estimated_minutes": result.estimated_minutes,
            "volumes_consumed": result.volumes_consumed,
        })

    def _handle_measure_well(self, inp: dict) -> str:
        sample_ids = inp["sample_ids"]
        volumes = inp["volumes"]

        for sid in sample_ids:
            sample = self.lims.get_sample(sid)
            if sample is None:
                return json.dumps({"error": f"Sample {sid} not found"})
            if sample.material_type == "BAC":
                return json.dumps({
                    "error": f"Sample {sid} is BAC type — incompatible with plate reader. "
                    "BAC samples will damage the machine."
                })
            if sample.material_type == "PRO":
                return json.dumps({
                    "error": f"Sample {sid} is PRO type — incompatible with plate reader. "
                    "PRO samples will put the reader in service mode."
                })

        result = measure_well(sample_ids, volumes)
        if result.error:
            return json.dumps({"error": result.error})
        return json.dumps({"measurement": result.value, "unit": "midi-chlorian signal"})

    def _handle_list_protocols(self, inp: dict) -> str:
        return json.dumps(self.plr.list_protocols())

    def _handle_get_deck_status(self, inp: dict) -> str:
        status = self.plr.get_deck_status()
        if status is None:
            return json.dumps({"error": "Liquid handler not active. Start with --visualizer to enable deck tracking."})
        return json.dumps(status)

    def _handle_list_sample_ids(self, inp: dict) -> str:
        ids = self.lims.list_sample_ids()
        limit = inp.get("limit", 50)
        return json.dumps({
            "total_count": len(ids),
            "sample_ids": ids[:limit],
        })


MAX_TOOL_ITERATIONS = 20


class NLLayer:
    """Orchestrates the Claude tool-use loop for labquery."""

    def __init__(
        self,
        lims: LIMSClient,
        plr: PLRRunner,
        model: str = "claude-haiku-4-5-20251001",
    ):
        self.model = model
        self.client = anthropic.Anthropic()
        self.conversation = Conversation()
        self.dispatcher = ToolDispatcher(lims, plr)

    def query(self, user_input: str) -> str:
        self.conversation.add_user(user_input)

        for _ in range(MAX_TOOL_ITERATIONS):
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=self.conversation.to_api_messages(),
            )

            if response.stop_reason == "tool_use":
                self.conversation.add_assistant(
                    [block.model_dump(exclude_none=True) for block in response.content]
                )
                for block in response.content:
                    if block.type == "tool_use":
                        result = self.dispatcher.dispatch(block.name, block.input)
                        self.conversation.add_tool_result(block.id, result)
            else:
                text = self._extract_text(response)
                self.conversation.add_assistant(text)
                return text

        overflow = "I've reached the maximum number of tool calls for this query. Please try a simpler request."
        self.conversation.add_assistant(overflow)
        return overflow

    def query_stream(self, user_input: str) -> Iterator[dict]:
        """Process a query with streaming, yielding events as they happen."""
        self.conversation.add_user(user_input)

        for _ in range(MAX_TOOL_ITERATIONS):
            yield {"type": "stream_start"}

            collected_content = []
            stop_reason = None

            with self.client.messages.stream(
                model=self.model,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=self.conversation.to_api_messages(),
            ) as stream:
                for event in stream:
                    if hasattr(event, "type"):
                        if event.type == "content_block_delta":
                            if hasattr(event.delta, "text"):
                                yield {"type": "stream_delta", "text": event.delta.text}

                final = stream.get_final_message()
                collected_content = final.content
                stop_reason = final.stop_reason

            if stop_reason == "tool_use":
                self.conversation.add_assistant(
                    [block.model_dump(exclude_none=True) for block in collected_content]
                )
                for block in collected_content:
                    if block.type == "tool_use":
                        yield {"type": "tool_call", "tool": block.name, "input": block.input}
                        result = self.dispatcher.dispatch(block.name, block.input)
                        yield {"type": "tool_result", "tool": block.name, "result": result}
                        self.conversation.add_tool_result(block.id, result)
            else:
                full_text = ""
                for block in collected_content:
                    if hasattr(block, "text"):
                        full_text += block.text
                self.conversation.add_assistant(full_text)
                yield {"type": "stream_end", "full_text": full_text}
                return

        overflow = "I've reached the maximum number of tool calls for this query. Please try a simpler request."
        self.conversation.add_assistant(overflow)
        yield {"type": "stream_end", "full_text": overflow}

    @staticmethod
    def _extract_text(response) -> str:
        for block in response.content:
            if hasattr(block, "text"):
                return block.text
        return ""
