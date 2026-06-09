import httpx
import asyncio
import inspect
import json

from src.codewalk.mcp.server import _TOOL_MAP

# Reuse the shared map — single source of truth
_TOOL_FUNCTIONS = _TOOL_MAP

# ── FastAPI HTTP backend (frontend path) ──
_TOOL_TO_ROUTE = {
    "codewalk_analyze_codebase": ("POST", "/analyze"),
    # codewalk_search_codebase has no equivalent raw-search API endpoint
    # (use /chat for Q&A, or call MCP tool directly)
    "codewalk_search_codebase": None,
    "codewalk_get_overview": ("GET", "/overview"),
    "codewalk_get_module_info": ("GET", "/modules/{module_name}"),
    "codewalk_get_blast_radius_map": ("GET", "/blast-radius"),
    "codewalk_get_reading_order": ("GET", "/reading-order"),
    "codewalk_get_execution_flow": ("GET", "/execution-flow"),
    "codewalk_incremental_reindex": ("POST", "/incremental-reindex"),
    "codewalk_refresh_analysis": ("POST", "/refresh"),
    "codewalk_review_diff": ("POST", "/review"),
    "codewalk_review_file": ("POST", "/review/file"),
    "codewalk_load_guidelines": ("POST", "/review/guidelines"),
    "codewalk_get_architecture_health": ("GET", "/architecture"),
    "codewalk_index_docs": ("POST", "/docs/index"),
    "codewalk_search_docs": ("POST", "/docs/search"),
    "codewalk_ask_docs": ("POST", "/docs/ask"),
    # codewalk_call_chain has no API endpoint — uses execute_direct only
}

def execute_direct(tool_name: str, arguments: dict) -> str:
    """Call a Codewalk tool function directly (same process).

    Args:
        tool_name: e.g. "codewalk_get_reading_order"
        arguments: e.g. {"module_name": "analysis"}

    Returns:
        Tool result as a string.
    """
    fn = _TOOL_FUNCTIONS.get(tool_name)
    if not fn:
        return f"Unknown tool: {tool_name}"
    
    # Remove None values and args the function doesn't accept
    # (tiny routing models sometimes hallucinate extra parameters)
    valid_params = set(inspect.signature(fn).parameters.keys())
    clean_args = {k: v for k, v in arguments.items() if v is not None and k in valid_params}
    return fn(**clean_args)

async def execute_mcp(tool_name: str, arguments: dict) -> str:
    """Call a Codewalk tool via MCP protocol over stdio.

    Spawns the MCP server as a subprocess if not already running.
    """
    from mcp.client.stdio import stdio_client
    from mcp import ClientSession, StdioServerParameters

    server_params = StdioServerParameters(
        command="python",
        args=["-m", "src.codewalk.mcp.server"]
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)
            return result.content[0].text
        
def execute_mcp_sync(tool_name: str, arguments: dict) -> str:
    """Sync wrapper for MCP execution."""
    return asyncio.run(execute_mcp(tool_name, arguments))

def execute_api(tool_name: str, arguments: dict, base_url: str = "http://localhost:8000") -> str:
    """Call a Codewalk tool via FastAPI HTTP endpoint.

    Args:
        tool_name: e.g. "codewalk_get_reading_order"
        arguments: e.g. {"module_name": "analysis"}
        base_url: FastAPI server URL.

    Returns:
        JSON response as string.
    """
    route_info = _TOOL_TO_ROUTE.get(tool_name)
    if route_info is None:
        return f"No API route for tool: {tool_name}"
    
    method, path = route_info

    # Substitute path parameters like {module_name}
    for key, value in arguments.items():
        if f"{{{key}}}" in path:
            path = path.replace(f"{{{key}}}", str(value))

    url = f"{base_url}{path}"

    with httpx.Client(timeout=60) as client:
        if method == "GET":
            response = client.get(url, params=arguments)
        else:
            response = client.post(url, json=arguments)

    response.raise_for_status()
    return json.dumps(response.json(), indent=2)