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

from labquery.lims_client import LIMSClient, RunRecord
from labquery.measure import measure_well, _find_measure_binary
from labquery.notify import SlackNotifier
from labquery.plr_runner import PLRRunner
from labquery.tools import TOOLS, build_system_prompt
from labquery.well_utils import expand_well_list, validate_wells


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

    def __init__(self, lims: LIMSClient, plr: PLRRunner, notifier: SlackNotifier | None = None):
        self.lims = lims
        self.plr = plr
        self.notifier = notifier or SlackNotifier()

    def dispatch(self, tool_name: str, tool_input: dict) -> str:
        handlers = {
            "query_sample_status": self._handle_query_sample,
            "check_inventory": self._handle_check_inventory,
            "run_protocol": self._handle_run_protocol,
            "list_sample_ids": self._handle_list_sample_ids,
            "measure_well": self._handle_measure_well,
            "list_protocols": self._handle_list_protocols,
            "get_deck_status": self._handle_get_deck_status,
            "get_run_history": self._handle_get_run_history,
            "transfer": self._handle_transfer,
            "aspirate_dispense": self._handle_aspirate_dispense,
            "get_well_contents": self._handle_get_well_contents,
            "search_samples": self._handle_search_samples,
            "create_sample": self._handle_create_sample,
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
        if self.plr.bridge_ready:
            async_handlers = {
                "run_protocol": self._handle_run_protocol_async,
                "transfer": self._handle_transfer_async,
                "aspirate_dispense": self._handle_aspirate_dispense_async,
            }
            handler = async_handlers.get(tool_name)
            if handler:
                try:
                    return await handler(tool_input)
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

        self._post_run(result, samples)
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

        self._post_run(result, samples)
        return self._format_run_result(result, samples)

    def _post_run(self, result, samples) -> None:
        for sid, consumed_ul in result.volumes_consumed.items():
            s = self.lims.get_sample(sid)
            if s:
                self.lims.update_sample_volume(sid, s.volume_ul - consumed_ul)

        if result.status == "completed":
            self.lims.record_run(RunRecord(
                run_id=result.run_id,
                protocol_name=result.protocol_name,
                sample_ids=[s.sample_id for s in samples],
                started_at=result.started_at,
                completed_at=result.completed_at,
                status=result.status,
            ))
            self.notifier.notify_run_completed(
                run_id=result.run_id,
                protocol_name=result.protocol_name,
                sample_count=len(samples),
                estimated_minutes=result.estimated_minutes,
            )
        elif result.status.startswith("error"):
            self.notifier.notify_run_error(
                run_id=result.run_id,
                protocol_name=result.protocol_name,
                error=result.status,
            )

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
        self.notifier.notify_measurement(sample_ids, result.value)
        return json.dumps({"measurement": result.value, "unit": "midi-chlorian signal"})

    def _handle_transfer(self, inp: dict) -> str:
        source_wells = expand_well_list(inp["source_wells"])
        dest_wells = expand_well_list(inp["destination_wells"])

        error = self._validate_transfer_input(source_wells, dest_wells, inp["volume_ul"])
        if error:
            return error

        result = self.plr.execute_transfer(
            source_wells=source_wells,
            dest_wells=dest_wells,
            volume_ul=inp["volume_ul"],
            source_plate=inp.get("source_plate", "dest_plate"),
            dest_plate=inp.get("destination_plate", "dest_plate"),
            reuse_tips=inp.get("reuse_tips", False),
        )

        self._post_transfer(result, source_wells, dest_wells, inp["volume_ul"])
        return self._format_transfer_result(result)

    async def _handle_transfer_async(self, inp: dict) -> str:
        source_wells = expand_well_list(inp["source_wells"])
        dest_wells = expand_well_list(inp["destination_wells"])

        error = self._validate_transfer_input(source_wells, dest_wells, inp["volume_ul"])
        if error:
            return error

        result = await self.plr.execute_transfer_async(
            source_wells=source_wells,
            dest_wells=dest_wells,
            volume_ul=inp["volume_ul"],
            source_plate=inp.get("source_plate", "dest_plate"),
            dest_plate=inp.get("destination_plate", "dest_plate"),
            reuse_tips=inp.get("reuse_tips", False),
        )

        self._post_transfer(result, source_wells, dest_wells, inp["volume_ul"])
        return self._format_transfer_result(result)

    def _validate_transfer_input(
        self, source_wells: list[str], dest_wells: list[str], volume_ul: float
    ) -> str | None:
        if len(source_wells) != len(dest_wells):
            return json.dumps({
                "error": f"Source ({len(source_wells)}) and destination ({len(dest_wells)}) well counts must match.",
            })
        if volume_ul <= 0:
            return json.dumps({"error": "Volume must be positive."})
        invalid = validate_wells(source_wells) + validate_wells(dest_wells)
        if invalid:
            return json.dumps({"error": f"Invalid well positions: {', '.join(invalid)}"})
        return None

    def _post_transfer(self, result, source_wells, dest_wells, volume_ul) -> None:
        self.lims.record_run(RunRecord(
            run_id=result.run_id,
            protocol_name="ad_hoc_transfer",
            sample_ids=[],
            started_at=result.started_at,
            completed_at=result.completed_at,
            status=result.status,
            notes=json.dumps({
                "source_wells": source_wells,
                "destination_wells": dest_wells,
                "volume_ul": volume_ul,
                "tips_used": result.tips_used,
            }),
        ))
        if result.status == "completed":
            self.notifier.notify_run_completed(
                run_id=result.run_id,
                protocol_name="ad_hoc_transfer",
                sample_count=result.wells_processed,
                estimated_minutes=0,
            )
        elif result.status.startswith("error") or result.status == "partial_error":
            self.notifier.notify_run_error(
                run_id=result.run_id,
                protocol_name="ad_hoc_transfer",
                error=result.error_detail or result.status,
            )

    @staticmethod
    def _format_transfer_result(result) -> str:
        return json.dumps({
            "run_id": result.run_id,
            "operation": result.operation,
            "status": result.status,
            "wells_processed": result.wells_processed,
            "volumes_moved": result.volumes_moved,
            "tips_used": result.tips_used,
            "error_detail": result.error_detail or None,
        })

    def _handle_aspirate_dispense(self, inp: dict) -> str:
        steps = inp["steps"]
        error = self._validate_aspirate_dispense_input(steps)
        if error:
            return error

        result = self.plr.execute_aspirate_dispense(
            steps=steps,
            new_tip_between_steps=inp.get("new_tip_between_steps", False),
        )

        self._post_aspirate_dispense(result, steps)
        return self._format_transfer_result(result)

    async def _handle_aspirate_dispense_async(self, inp: dict) -> str:
        steps = inp["steps"]
        error = self._validate_aspirate_dispense_input(steps)
        if error:
            return error

        result = await self.plr.execute_aspirate_dispense_async(
            steps=steps,
            new_tip_between_steps=inp.get("new_tip_between_steps", False),
        )

        self._post_aspirate_dispense(result, steps)
        return self._format_transfer_result(result)

    def _validate_aspirate_dispense_input(self, steps: list[dict]) -> str | None:
        if not steps:
            return json.dumps({"error": "Steps list cannot be empty."})
        for i, step in enumerate(steps):
            if step.get("action") not in ("aspirate", "dispense"):
                return json.dumps({"error": f"Step {i + 1}: action must be 'aspirate' or 'dispense'."})
            if step.get("volume_ul", 0) <= 0:
                return json.dumps({"error": f"Step {i + 1}: volume must be positive."})
            invalid = validate_wells([step["well"]])
            if invalid:
                return json.dumps({"error": f"Step {i + 1}: invalid well {step['well']!r}."})
        return None

    def _post_aspirate_dispense(self, result, steps) -> None:
        self.lims.record_run(RunRecord(
            run_id=result.run_id,
            protocol_name="ad_hoc_aspirate_dispense",
            sample_ids=[],
            started_at=result.started_at,
            completed_at=result.completed_at,
            status=result.status,
            notes=json.dumps({"steps": steps, "tips_used": result.tips_used}),
        ))
        if result.status == "completed":
            self.notifier.notify_run_completed(
                run_id=result.run_id,
                protocol_name="ad_hoc_aspirate_dispense",
                sample_count=result.wells_processed,
                estimated_minutes=0,
            )
        elif result.status.startswith("error") or result.status == "partial_error":
            self.notifier.notify_run_error(
                run_id=result.run_id,
                protocol_name="ad_hoc_aspirate_dispense",
                error=result.error_detail or result.status,
            )

    def _handle_get_well_contents(self, inp: dict) -> str:
        plate = inp.get("plate", "dest_plate")
        wells = inp.get("wells")
        if wells:
            wells = expand_well_list(wells)
        result = self.plr.get_well_contents(plate_name=plate, wells=wells)
        if result is None:
            return json.dumps({"error": "Liquid handler not active."})
        return json.dumps(result)

    def _handle_list_protocols(self, inp: dict) -> str:
        return json.dumps(self.plr.list_protocols())

    def _handle_get_deck_status(self, inp: dict) -> str:
        status = self.plr.get_deck_status()
        if status is None:
            return json.dumps({"error": "Liquid handler not active. Start with --visualizer to enable deck tracking."})
        return json.dumps(status)

    def _handle_list_sample_ids(self, inp: dict) -> str:
        limit = inp.get("limit", 50)
        material_type = inp.get("material_type")
        if material_type:
            samples = self.lims.list_samples(sample_type=material_type)
            ids = [s.sample_id for s in samples]
        else:
            ids = self.lims.list_sample_ids()
        return json.dumps({
            "total_count": len(ids),
            "sample_ids": ids[:limit],
        })

    def _handle_search_samples(self, inp: dict) -> str:
        query = inp.get("query", "")
        limit = inp.get("limit", 20)
        samples = self.lims.search_samples(query, limit)
        return json.dumps({
            "count": len(samples),
            "samples": [
                {
                    "sample_id": s.sample_id,
                    "material_type": s.material_type,
                    "volume_ul": s.volume_ul,
                }
                for s in samples
            ],
        })

    def _handle_create_sample(self, inp: dict) -> str:
        material_type = inp.get("material_type", "")
        volume_ul = inp.get("volume_ul", 1000.0)
        concentration = inp.get("concentration", 0.0)
        sample = self.lims.create_sample(material_type, volume_ul, concentration)
        if sample is None:
            return json.dumps({"error": "Failed to create sample. This LIMS backend may not support sample creation."})
        return json.dumps({
            "sample_id": sample.sample_id,
            "material_type": sample.material_type,
            "volume_ul": sample.volume_ul,
        })

    def _handle_get_run_history(self, inp: dict) -> str:
        runs = self.lims.get_run_history(sample_id=inp.get("sample_id"))
        return json.dumps([
            {
                "run_id": r.run_id,
                "protocol_name": r.protocol_name,
                "sample_ids": r.sample_ids,
                "started_at": r.started_at.isoformat(),
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "status": r.status,
                "notes": r.notes,
            }
            for r in runs
        ])


MAX_TOOL_ITERATIONS = 20


class NLLayer:
    """Orchestrates the Claude tool-use loop for labquery."""

    def __init__(
        self,
        lims: LIMSClient,
        plr: PLRRunner,
        model: str = "claude-haiku-4-5-20251001",
        notifier: SlackNotifier | None = None,
    ):
        self.model = model
        self.client = anthropic.Anthropic()
        self.conversation = Conversation()
        self.dispatcher = ToolDispatcher(lims, plr, notifier=notifier)
        has_plate_reader = _find_measure_binary() is not None
        self.tools = [t for t in TOOLS if t["name"] != "measure_well" or has_plate_reader]
        self.system_prompt = build_system_prompt(has_plate_reader=has_plate_reader)

    def query(self, user_input: str) -> str:
        self.conversation.add_user(user_input)

        for _ in range(MAX_TOOL_ITERATIONS):
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=self.system_prompt,
                tools=self.tools,
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
                system=self.system_prompt,
                tools=self.tools,
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
