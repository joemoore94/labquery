# labquery

Natural language interface for LIMS and liquid handler automation.

Ask questions like "where is sample C6OT0FN3S?" or "run the CEL/DNA protocol on these samples" and get real answers backed by your LIMS and PyLabRobot.

## Architecture

```
User (natural language) -> NL Layer (Claude tool use) -> LIMS Client / PLR Runner -> Response
```

- **NL Layer** (`nl_layer.py`) — Claude function-calling loop that parses intent and dispatches to tools
- **LIMS Client** (`lims_client.py`) — Abstract interface + labio-all REST implementation
- **PLR Runner** (`plr_runner.py`) — Protocol execution via PyLabRobot (simulator in Phase 1)
- **Tools** (`tools.py`) — Claude tool definitions for sample queries, inventory checks, protocol runs, and run history

## Setup

```bash
python3 -m venv labquery-env
source labquery-env/bin/activate
pip install -e ".[dev]"
```

Set your API key:

```bash
export ANTHROPIC_API_KEY=your_key_here
```

Start labio-all (LIMS backend):

```bash
git clone https://github.com/smohler/labio-all.git
cd labio-all && chmod +x run.sh && ./run.sh
```

## Usage

Interactive mode:

```bash
labquery
```

Single query:

```bash
labquery "where is sample C6OT0FN3S?"
```

## Examples

```
labquery> Where is sample C6OT0FN3S right now?
Sample C6OT0FN3S (CEL type) is in Rack 3, Position A4. Current volume: 450ul.

labquery> Do we have enough CEL samples for a 384-well plate run?
You have 312 CEL samples with sufficient volume. A 384-well run requires 384. You are 72 short.

labquery> Run the CEL/DNA combination protocol on samples 47B, 52A, and 61C
Protocol initiated. 3 samples queued. Estimated completion: 4.5 minutes.
```

## Testing

```bash
pytest
```

Tests use an in-memory LIMS fake — no running services required.

## Project Status

**Phase 1** (current): LIMS query layer, simulated PLR execution, Claude NL interface, CLI.

**Phase 2** (next): Wire PLR simulator backend, real LIMS update loop, end-to-end integration tests.

**Phase 3**: Slack notifications, demo notebook, community post to labautomation.io.

## License

MIT
