"""
=============================================================================
 backends.py - Tool Execution Backends
=============================================================================

WHAT THIS FILE DOES:
    Provides 3 ways to execute Codewalk tools from the voice companion:
    1. execute_direct(): call Python functions directly (fastest, same process)
    2. execute_mcp()/execute_mcp_sync(): call via MCP protocol over stdio
    3. execute_api(): call via FastAPI HTTP endpoints

WHY MULTIPLE BACKENDS:
    - Direct: for CLI companion (no server needed)
    - MCP: for testing MCP protocol compatibility
    - API: for web frontend (browser talks to FastAPI)

WHERE IT'S CALLED:
    - companion.py -> main() picks backend based on --backend flag

DEPENDENCIES:
    - mcp/server.py: _TOOL_MAP for direct execution
    - httpx: for HTTP calls to FastAPI

=============================================================================
"""

import httpx
import asyncio
import inspect
import json

from src.codewalk.mcp.server import _TOOL_MAP

# Reuse the shared tool map from MCP server
_TOOL_FUNCTIONS = _TOOL_MAP

# Maps tool names to FastAPI routes (for API backend)
_TOOL_TO_ROUTE = {
    "codewalk_analyze_codebase": ("POST", "/analyze"),
    "codewalk_search_codebase": ("POST", "/chat"),
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
}


def execute_direct(tool_name: str, arguments: dict) -> str:
    """Call a Codewalk tool function directly (same process, fastest).

    Cleans up arguments to only pass valid params (routing models
    sometimes hallucinate extra parameters).

    EXAMPLE TRACE:
        tool_name  = "codewalk_explain_function"
        arguments  = {"function_name": "scan_directory", "extra_param": "junk"}
        fn         = codewalk_explain_function   # from _TOOL_MAP
        valid_params = {"function_name"}          # from inspect.signature
        clean_args   = {"function_name": "scan_directory"}  # "extra_param" dropped
        return → fn(function_name="scan_directory")  → "## scan_directory\n..."
    """
    fn = _TOOL_FUNCTIONS.get(tool_name)
    if not fn:
        return f"Unknown tool: {tool_name}"

    # Only pass args the function actually accepts
    valid_params = set(inspect.signature(fn).parameters.keys())
    clean_args = {k: v for k, v in arguments.items() if v is not None and k in valid_params}
    return fn(**clean_args)


async def execute_mcp(tool_name: str, arguments: dict) -> str:
    """Call a Codewalk tool via MCP protocol over stdio subprocess."""
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

    Substitutes path parameters (e.g. {module_name}) and routes
    to the correct HTTP method/path.

    EXAMPLE TRACE:
        tool_name  = "codewalk_get_module_info"
        arguments  = {"module_name": "analysis"}
        route_info = ("GET", "/modules/{module_name}")
        path       = "/modules/analysis"              # {module_name} substituted
        url        = "http://localhost:8000/modules/analysis"
        response   = client.get(url, params={"module_name": "analysis"})  → 200
        return     → '{"name": "analysis", "file_count": 7, ...}'
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
    return response.text