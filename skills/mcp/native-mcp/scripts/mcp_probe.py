"""Out-of-process MCP stdio handshake probe.

Spawns each server exactly as the gateway would (command + args + env mapped from
~/.hermes/.env), runs initialize + list_tools, prints tool count. Run with the
Hermes runtime venv python (has the `mcp` package):

  V=$(ls -d ~/.hermes/installs/*/environments/*/venv/bin/python3 | head -1)
  cd ~/.hermes/cache/scratch && timeout 200 $V mcp_probe.py 2>&1 | grep -E '^(ok|fail)'

Edit SERVERS: key = server name; value = (args, {ENV_VAR_THE_PACKAGE_READS: .env key}).
Secrets are never printed; grep stdout for the ok/fail lines to drop server stderr.
"""
import asyncio, os
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

DOTENV = os.path.expanduser("~/.hermes/.env")

# server name -> (npx args, {env var the PACKAGE reads: key in .env})
SERVERS = {
    "sentry": (["-y", "@sentry/mcp-server"], {"SENTRY_ACCESS_TOKEN": "SENTRY_AUTH_TOKEN"}),
    "apify": (["-y", "@apify/actors-mcp-server"], {"APIFY_TOKEN": "APIFY_API_TOKEN"}),
}


def load_env(path):
    env = {}
    for line in open(path):
        if "=" in line and not line.startswith("#"):
            k, v = line.rstrip("\n").split("=", 1)
            env[k] = v.strip().strip('"').strip("'")
    return env


async def probe(name, args, env_map, dotenv):
    sub_env = {**os.environ, **{pkg_var: dotenv.get(src, "") for pkg_var, src in env_map.items()}}
    missing = [src for src in env_map.values() if not dotenv.get(src)]
    if missing:
        print(f"fail {name}: .env missing {missing}")
        return
    params = StdioServerParameters(command="npx", args=args, env=sub_env)
    try:
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await asyncio.wait_for(s.initialize(), 40)
                tools = await asyncio.wait_for(s.list_tools(), 40)
                names = [t.name for t in tools.tools]
                print(f"ok {name}: {len(names)} tools: {names[:6]}")
    except Exception as ex:  # ExceptionGroup on early exit = read the stderr above this line
        print(f"fail {name}: {type(ex).__name__}: {str(ex)[:200]}")


async def main():
    dotenv = load_env(DOTENV)
    for name, (args, env_map) in SERVERS.items():
        await asyncio.wait_for(probe(name, args, env_map, dotenv), 90)


asyncio.run(main())
