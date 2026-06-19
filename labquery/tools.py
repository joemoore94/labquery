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
]

SYSTEM_PROMPT = (
    "You are labquery, a natural language assistant for pharmaceutical lab automation. "
    "You help scientists query their LIMS (Laboratory Information Management System) "
    "and control liquid handlers via PyLabRobot.\n\n"
    "The LIMS contains ~10,000 samples with material types: CEL, DNA, BAC, PRO. "
    "Each sample has a volume (uL), concentration (mg/ml), labware info, and a "
    "sequence URL.\n\n"
    "When a user asks about samples, inventory, or protocols, use the available tools "
    "to look up real data. Respond in clear, concise language a bench scientist would "
    "understand.\n\n"
    "For destructive actions (running protocols), confirm with the user before proceeding."
)
