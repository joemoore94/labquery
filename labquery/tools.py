"""Claude tool definitions for the labquery NL interface.

Each tool maps to a real action the system can take: querying the LIMS,
checking inventory, or running protocols.
"""

TOOLS = [
    {
        "name": "query_sample_status",
        "description": (
            "Look up the current volume, material type, concentration, and labware "
            "for a sample in the LIMS. Use this when the user asks about a specific sample."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sample_id": {
                    "type": "string",
                    "description": "The sample ID to look up, e.g. C6OT0FN3S",
                },
            },
            "required": ["sample_id"],
        },
    },
    {
        "name": "check_inventory",
        "description": (
            "Check how many samples of a given material type are available with sufficient volume. "
            "Use this when the user asks if there are enough samples, how many are available, "
            "or whether a plate run is feasible."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sample_type": {
                    "type": "string",
                    "description": "The material type to check: CEL, DNA, BAC, or PRO",
                },
                "min_volume_ul": {
                    "type": "number",
                    "description": "Minimum volume in microliters a sample must have to count as available",
                    "default": 50,
                },
                "required_count": {
                    "type": "integer",
                    "description": "How many samples are needed (omit to just get a count)",
                },
            },
            "required": ["sample_type"],
        },
    },
    {
        "name": "run_protocol",
        "description": (
            "Execute a liquid handling protocol on a set of samples via PyLabRobot. "
            "Use this when the user wants to run, start, or execute a protocol on specific samples."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "protocol_name": {
                    "type": "string",
                    "description": "Name of the protocol to run, e.g. 'CEL/DNA combination'",
                },
                "sample_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of sample IDs to include in the run",
                },
            },
            "required": ["protocol_name", "sample_ids"],
        },
    },
    {
        "name": "list_sample_ids",
        "description": (
            "List all sample IDs in the LIMS. Use this when the user wants to browse "
            "available samples or needs to find sample IDs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of IDs to return (default 50)",
                    "default": 50,
                },
            },
        },
    },
    {
        "name": "measure_well",
        "description": (
            "Run a plate reader measurement on a well containing one or more samples. "
            "Takes sample IDs and volumes, returns a midi-chlorian signal reading. "
            "WARNING: Only CEL and DNA samples are compatible — BAC samples will damage "
            "the plate reader, and PRO samples will put it in service mode. Always check "
            "sample types before measuring."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sample_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Sample IDs in the well",
                },
                "volumes": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "Volume in uL of each sample added to the well",
                },
            },
            "required": ["sample_ids", "volumes"],
        },
    },
    {
        "name": "list_protocols",
        "description": (
            "List all available liquid handling protocols that can be run on the OT-2. "
            "Use this before running a protocol to check what's available and what "
            "volume each protocol consumes per sample."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_deck_status",
        "description": (
            "Check the current status of the liquid handler deck: how many tips remain "
            "in each rack, total tips available. Use this when the user asks about tips, "
            "deck status, or consumables."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_run_history",
        "description": (
            "Retrieve the history of protocol runs, optionally filtered by sample ID. "
            "Use this when the user asks what happened to a sample, what runs have been "
            "done, or wants to see protocol execution history."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sample_id": {
                    "type": "string",
                    "description": "Filter runs to only those involving this sample ID. Omit to get all runs.",
                },
            },
        },
    },
]

SYSTEM_PROMPT = (
    "You are labquery, a natural language assistant for pharmaceutical lab automation. "
    "You help scientists query their LIMS (Laboratory Information Management System), "
    "control liquid handlers via PyLabRobot, and run plate reader measurements.\n\n"
    "You have access to three systems:\n"
    "- LIMS: tracks ~10,000 samples with material types (CEL, DNA, BAC, PRO), "
    "volumes (uL), concentrations (mg/ml), labware info, and sequence URLs.\n"
    "- Liquid handler (PyLabRobot): an OT-2 simulator with a tube rack, destination "
    "plate, and two 96-tip racks. You can check deck status including tip counts.\n"
    "- Plate reader: measures midi-chlorian signal from well contents. ONLY CEL and DNA "
    "samples are compatible. BAC samples BREAK the reader. PRO samples put it in service "
    "mode. Always verify sample types before measuring.\n\n"
    "When a user asks about samples or inventory, query the LIMS. When they ask about "
    "tips, deck status, or consumables, check the liquid handler. Use the available "
    "tools to look up real data. Respond in clear, concise language a bench scientist "
    "would understand.\n\n"
    "Pay close attention to conversation history. When the user says \"this sample\", "
    "\"that one\", \"it\", or refers to something previously discussed, resolve the "
    "reference from the conversation context. Never ask the user to repeat a sample ID "
    "or piece of information that was already mentioned in the conversation.\n\n"
    "For destructive actions (running protocols, measurements), confirm with the user "
    "before proceeding."
)
