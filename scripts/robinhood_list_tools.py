#!/usr/bin/env python3
"""Connects to the Robinhood Trading MCP with already-saved credentials
(run scripts/robinhood_oauth_setup.py first) and prints every available
tool's name, description, and input/output schema.

Compare this against the tool-name/field-name assumptions hardcoded in
app/brokers/robinhood_broker.py (see the mapping comment at the top of
that file) before trusting live trading with real money. Tool *names*
were confirmed against Robinhood's own support docs, but the exact
input/output field names in robinhood_broker.py are our best guess
until checked against this output.

Run:
    docker compose exec day-trader-app python scripts/robinhood_list_tools.py

Uses the same headless connection path RobinhoodBroker uses in
production, so this is also a real end-to-end check that the saved
credentials actually work, not just a schema dump.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.brokers.robinhood_oauth import RobinhoodAuthError, open_session  # noqa: E402

# Tools app/brokers/robinhood_broker.py actually calls - check these first.
USED_BY_BROKER = {
    "get_accounts",
    "get_portfolio",
    "get_equity_positions",
    "review_equity_order",
    "place_equity_order",
}


def _print_tool(tool) -> None:
    flag = "  <-- used by robinhood_broker.py" if tool.name in USED_BY_BROKER else ""
    print(f"\n=== {tool.name}{flag} ===")
    if tool.description:
        print(tool.description.strip())
    input_schema = getattr(tool, "inputSchema", None)
    if input_schema:
        print("input schema:")
        print(json.dumps(input_schema, indent=2))
    output_schema = getattr(tool, "outputSchema", None)
    if output_schema:
        print("output schema:")
        print(json.dumps(output_schema, indent=2))


async def main():
    async with open_session() as session:
        result = await session.list_tools()

    used = [t for t in result.tools if t.name in USED_BY_BROKER]
    other = [t for t in result.tools if t.name not in USED_BY_BROKER]

    print(f"{len(result.tools)} tools available. Showing tools used by "
          f"robinhood_broker.py first, then everything else.")

    for tool in used:
        _print_tool(tool)

    missing = USED_BY_BROKER - {t.name for t in used}
    if missing:
        print(f"\n!!! robinhood_broker.py references tools NOT in this server's "
              f"tool list - fix app/brokers/robinhood_broker.py: {sorted(missing)}")

    print("\n--- remaining tools ---")
    for tool in other:
        _print_tool(tool)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RobinhoodAuthError as e:
        print(f"Auth error: {e}")
        raise SystemExit(1)
