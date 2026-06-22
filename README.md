# labquery

Natural language interface for LIMS and liquid handler automation. Ask questions about your samples, run protocols, and measure plates through a chat interface backed by Claude tool use, labio-all, and PyLabRobot.

## Setup

```bash
python3 -m venv labquery-env
source labquery-env/bin/activate
pip install -e ".[dev]"
export ANTHROPIC_API_KEY=your_key_here
```

## Usage

Start the chat UI with the simulator (auto-starts labio-all and PLR):

```bash
labquery --simulator --serve
```

Select a liquid handler backend:

```bash
labquery --simulator --backend tecan --serve
labquery --simulator --backend hamilton --serve
labquery --simulator --backend opentrons --serve   # default
```

Add `--visualizer` to open the PLR deck viewer (Opentrons only for now):

```bash
labquery --simulator --serve --visualizer
```

Single query from the command line:

```bash
labquery --simulator "how many CEL samples are available?"
```

Interactive REPL:

```bash
labquery --simulator
```

Connect to Benchling:

```bash
export BENCHLING_URL=https://mycompany.benchling.com
export BENCHLING_API_KEY=sk_...
pip install labquery[benchling]
labquery --lims benchling --serve
```

Connect to an existing labio-all instance:

```bash
labquery --lims labio --lims-url http://your-lims:5001 --serve
```

## What it does

- **Sample queries** -- look up location, volume, concentration, material type
- **Inventory checks** -- count available samples by type, check if you have enough for a run
- **Protocol execution** -- run liquid handling protocols (CEL/DNA combination, serial dilution, sample transfer) with automatic LIMS volume writeback
- **Plate reader measurements** -- measure midi-chlorian signal with BAC/PRO safety guards
- **Deck status** -- check tip counts and rack state on the liquid handler

## Supported backends

| Backend | Deck | Simulator | Hardware |
| --- | --- | --- | --- |
| `opentrons` (default) | OT-2 | OT-2 Simulator | stubbed |
| `tecan` | EVO 150 | ChatterBox | stubbed |
| `hamilton` | STARLet | ChatterBox | stubbed |

Hardware backends are defined via PyLabRobot but gated until tested on real machines.

## Architecture

```
labquery/
  cli.py           -- entry point, CLI flags, mode selection
  nl_layer.py      -- Claude tool-use loop and ToolDispatcher
  tools.py         -- tool definitions and system prompt
  lims_client.py   -- abstract LIMSClient + labio-all REST implementation
  plr_runner.py    -- protocol registry, simulated and bridge execution
  plr_bridge.py    -- BackendConfig presets, PLR bridge for all backends
  measure.py       -- plate reader binary interface
  labio_server.py  -- auto-clone and start labio-all as a subprocess
  ws_server.py     -- WebSocket chat server with streaming responses
  static/          -- browser chat UI
```

## Testing

```bash
pytest
```

Unit and integration tests run without any external services. The toy problem benchmark (`test_toy_problem.py`) requires labio-all running on localhost:5001 and skips automatically if it's not available.

## Project Status

- **Phase 1** (done): LIMS query layer, Claude NL interface, CLI, chat UI
- **Phase 2** (done): PLR simulator integration, multi-backend support, LIMS volume writeback, plate reader, integration tests, toy problem benchmark
- **Phase 3** (current): Slack notifications, demo notebook, community post

## License

MIT
