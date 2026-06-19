"""WebSocket chat server for labquery.

Runs a websockets async server for real-time chat alongside an HTTP server
for the static chat UI. Uses Claude's streaming API for live response rendering.
"""

from __future__ import annotations

import asyncio
import http.server
import json
import logging
import os
import threading
from pathlib import Path

import anthropic
import websockets
from dotenv import load_dotenv

from labquery.lims_client import LIMSClient
from labquery.nl_layer import Conversation, ToolDispatcher
from labquery.plr_runner import PLRRunner
from labquery.tools import SYSTEM_PROMPT, TOOLS

STATIC_DIR = Path(__file__).parent / "static"

log = logging.getLogger("labquery")


class ChatSession:
    """Per-connection state for a WebSocket chat client."""

    def __init__(self, dispatcher: ToolDispatcher, model: str):
        self.conversation = Conversation()
        self.dispatcher = dispatcher
        self.model = model
        self.client = anthropic.AsyncAnthropic()


class ChatServer:
    """WebSocket chat server with streaming Claude responses."""

    def __init__(
        self,
        lims: LIMSClient,
        plr: PLRRunner,
        model: str = "claude-haiku-4-5-20251001",
        host: str = "127.0.0.1",
        ws_port: int = 8765,
        http_port: int = 8080,
    ):
        self.lims = lims
        self.plr = plr
        self.model = model
        self.host = host
        self.ws_port = ws_port
        self.http_port = http_port
        self._sessions: dict[int, ChatSession] = {}

    async def start(self) -> None:
        load_dotenv()

        for port in range(self.ws_port, self.ws_port + 10):
            try:
                ws_server = await websockets.serve(
                    self._handle_connection, self.host, port
                )
                self.ws_port = port
                break
            except OSError:
                continue
        else:
            raise OSError(f"Could not find a free WebSocket port in range {self.ws_port}-{self.ws_port + 9}")

        self._start_http_server()
        print(f"Chat UI:   http://{self.host}:{self.http_port}")
        print(f"WebSocket: ws://{self.host}:{self.ws_port}")
        print("Press Ctrl+C to stop.\n")

        await asyncio.Future()

    def _start_http_server(self) -> None:
        """Start a static file server in a background thread."""

        chat_server = self

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args, directory=None, **kwargs):
                super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

            def do_GET(self):
                if self.path == "/config.json":
                    config = json.dumps({"ws_port": chat_server.ws_port}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", len(config))
                    self.end_headers()
                    self.wfile.write(config)
                    return
                super().do_GET()

            def log_message(self, format, *args):
                pass

        for port in range(self.http_port, self.http_port + 10):
            try:
                server = http.server.HTTPServer((self.host, port), Handler)
                self.http_port = port
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                return
            except OSError:
                continue

        raise OSError(f"Could not find a free port in range {self.http_port}-{self.http_port + 9}")

    async def _handle_connection(self, websocket) -> None:
        session_id = id(websocket)
        dispatcher = ToolDispatcher(self.lims, self.plr)
        session = ChatSession(dispatcher=dispatcher, model=self.model)
        self._sessions[session_id] = session
        log.info("Client connected (session %s)", session_id)

        try:
            async for raw in websocket:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await websocket.send(json.dumps({
                        "type": "error", "message": "Invalid JSON"
                    }))
                    continue

                if msg.get("type") == "message" and msg.get("text"):
                    log.info("User: %s", msg["text"])
                    await self._stream_response(session, websocket, msg["text"])
        except websockets.exceptions.ConnectionClosed:
            log.info("Client disconnected (session %s)", session_id)
        except Exception:
            log.exception("Connection error (session %s)", session_id)
        finally:
            self._sessions.pop(session_id, None)

    async def _stream_response(
        self, session: ChatSession, websocket, user_input: str
    ) -> None:
        session.conversation.add_user(user_input)

        while True:
            await websocket.send(json.dumps({"type": "stream_start"}))

            collected_content = []
            stop_reason = None

            try:
                async with session.client.messages.stream(
                    model=session.model,
                    max_tokens=1024,
                    system=SYSTEM_PROMPT,
                    tools=TOOLS,
                    messages=session.conversation.to_api_messages(),
                ) as stream:
                    async for event in stream:
                        if hasattr(event, "type") and event.type == "content_block_delta":
                            if hasattr(event.delta, "text"):
                                await websocket.send(json.dumps({
                                    "type": "stream_delta",
                                    "text": event.delta.text,
                                }))

                    final = await stream.get_final_message()
                    collected_content = final.content
                    stop_reason = final.stop_reason
            except Exception:
                log.exception("Claude API error")
                await websocket.send(json.dumps({
                    "type": "error",
                    "message": "Failed to get response from Claude. Check server logs.",
                }))
                return

            log.info("Stop reason: %s, content blocks: %d", stop_reason, len(collected_content))

            if stop_reason == "tool_use":
                session.conversation.add_assistant(
                    [block.model_dump() for block in collected_content]
                )
                for block in collected_content:
                    if block.type == "tool_use":
                        log.info("Tool call: %s(%s)", block.name, json.dumps(block.input)[:200])

                        await websocket.send(json.dumps({
                            "type": "tool_call",
                            "tool": block.name,
                            "input": block.input,
                        }))

                        if block.name == "run_protocol" and self.plr.bridge_ready:
                            result = await self._dispatch_async(
                                session.dispatcher, block.name, block.input
                            )
                        else:
                            result = session.dispatcher.dispatch(
                                block.name, block.input
                            )

                        log.info("Tool result: %s -> %s", block.name, result[:200])

                        await websocket.send(json.dumps({
                            "type": "tool_result",
                            "tool": block.name,
                            "result": result,
                        }))
                        session.conversation.add_tool_result(block.id, result)
            else:
                full_text = ""
                for block in collected_content:
                    if hasattr(block, "text"):
                        full_text += block.text
                log.info("Response: %s", full_text[:200] if full_text else "(empty)")
                session.conversation.add_assistant(full_text)
                await websocket.send(json.dumps({
                    "type": "stream_end",
                    "full_text": full_text,
                }))
                return

    async def _dispatch_async(
        self, dispatcher: ToolDispatcher, tool_name: str, tool_input: dict
    ) -> str:
        """Handle async tool dispatch for protocol runs when PLR bridge is active."""
        if tool_name == "run_protocol":
            samples = []
            missing = []
            for sid in tool_input["sample_ids"]:
                s = dispatcher.lims.get_sample(sid)
                if s is None:
                    missing.append(sid)
                else:
                    samples.append(s)

            if missing:
                return json.dumps({"error": f"Samples not found: {', '.join(missing)}"})

            result = await self.plr.run_protocol_async(
                protocol_name=tool_input["protocol_name"],
                samples=samples,
            )

            for sid, consumed_ul in result.volumes_consumed.items():
                s = dispatcher.lims.get_sample(sid)
                if s:
                    dispatcher.lims.update_sample_volume(sid, s.volume_ul - consumed_ul)

            return json.dumps({
                "run_id": result.run_id,
                "status": result.status,
                "samples_processed": len(samples),
                "estimated_minutes": result.estimated_minutes,
                "volumes_consumed": result.volumes_consumed,
            })

        return dispatcher.dispatch(tool_name, tool_input)
