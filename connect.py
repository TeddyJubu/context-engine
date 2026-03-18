#!/usr/bin/env python3
"""
connect.py — Auto-configure MCP server connections for popular coding agents.
"""

import argparse
import json
import os
import platform
import shutil
import sys
from pathlib import Path

# ── Constants ─────────────────────────────────────────────────────────────────

SERVER_NAME = "context-engine"
SCRIPT_DIR = Path(__file__).resolve().parent
VENV_PYTHON = SCRIPT_DIR / ".venv" / "bin" / "python3"
MCP_SERVER = SCRIPT_DIR / "mcp_server.py"

# ANSI colours (disabled on Windows or non-TTY)
_USE_COLOUR = sys.stdout.isatty() and platform.system() != "Windows"
DIM   = "\033[2m"   if _USE_COLOUR else ""
GREEN = "\033[32m"  if _USE_COLOUR else ""
RED   = "\033[31m"  if _USE_COLOUR else ""
RESET = "\033[0m"   if _USE_COLOUR else ""
BOLD  = "\033[1m"   if _USE_COLOUR else ""


# ── Agent definitions ─────────────────────────────────────────────────────────

def _claude_desktop_config() -> Path:
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    elif system == "Windows":
        appdata = os.environ.get("APPDATA", "")
        return Path(appdata) / "Claude" / "claude_desktop_config.json"
    else:
        return Path.home() / ".config" / "claude" / "claude_desktop_config.json"


def _claude_desktop_detected() -> bool:
    config = _claude_desktop_config()
    return config.parent.exists()


AGENTS = [
    {
        "id": "claude-code",
        "name": "Claude Code",
        "config": lambda: Path.home() / ".claude.json",
        "config_display": "~/.claude.json",
        "config_key": "mcpServers",
        "detected": lambda: (Path.home() / ".claude.json").exists() or shutil.which("claude") is not None,
    },
    {
        "id": "cursor",
        "name": "Cursor",
        "config": lambda: Path.home() / ".cursor" / "mcp.json",
        "config_display": "~/.cursor/mcp.json",
        "config_key": "mcpServers",
        "detected": lambda: (Path.home() / ".cursor").exists(),
    },
    {
        "id": "vscode",
        "name": "VS Code / Copilot",
        "config": lambda: Path.home() / ".vscode" / "mcp.json",
        "config_display": "~/.vscode/mcp.json",
        "config_key": "servers",
        "detected": lambda: (Path.home() / ".vscode").exists(),
    },
    {
        "id": "windsurf",
        "name": "Windsurf",
        "config": lambda: Path.home() / ".codeium" / "windsurf" / "mcp_config.json",
        "config_display": "~/.codeium/windsurf/mcp_config.json",
        "config_key": "mcpServers",
        "detected": lambda: (Path.home() / ".codeium" / "windsurf").exists(),
    },
    {
        "id": "claude-desktop",
        "name": "Claude Desktop",
        "config": _claude_desktop_config,
        "config_display": str(_claude_desktop_config()).replace(str(Path.home()), "~"),
        "config_key": "mcpServers",
        "detected": _claude_desktop_detected,
    },
]

AGENT_BY_ID = {a["id"]: a for a in AGENTS}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _entry() -> dict:
    return {
        "command": str(VENV_PYTHON),
        "args": [str(MCP_SERVER)],
    }


def _read_config(path: Path) -> dict | None:
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8").strip()
        return json.loads(text) if text else {}
    except json.JSONDecodeError as exc:
        print(f"{RED}  Error: {path} contains invalid JSON — {exc}{RESET}")
        return None


def _write_config(path: Path, data: dict, dry_run: bool = False) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(data, indent=2) + "\n"
    if dry_run:
        print(f"  [dry-run] Would write to {path}:")
        for line in content.splitlines():
            print(f"    {line}")
        return True
    # Backup existing file
    if path.exists():
        bak = Path(str(path) + ".bak")
        shutil.copy2(path, bak)
        display_bak = str(bak).replace(str(Path.home()), "~")
        print(f"  (backup: {display_bak})")
    path.write_text(content, encoding="utf-8")
    return True


def _connect_agent(agent: dict, dry_run: bool = False, auto_overwrite: bool = False) -> bool:
    config_path: Path = agent["config"]()
    config_key: str = agent["config_key"]
    display = agent["config_display"]
    name = agent["name"]

    data = _read_config(config_path)
    if data is None:
        print(f"  {RED}✗ Skipping {name} — config file is malformed.{RESET}")
        return False

    section = data.setdefault(config_key, {})

    if SERVER_NAME in section:
        if not auto_overwrite:
            ans = input(f"  {SERVER_NAME} already configured in {name}. Overwrite? [y/N] ").strip().lower()
            if ans != "y":
                print(f"  Skipped {name}.")
                return False
        else:
            print(f"  Note: overwriting existing {SERVER_NAME} entry in {name}.")

    section[SERVER_NAME] = _entry()
    _write_config(config_path, data, dry_run=dry_run)

    if not dry_run:
        print(f"  {GREEN}✓ {name} connected — {display}{RESET}")
    return True


def _disconnect_agent(agent: dict, dry_run: bool = False) -> bool:
    config_path: Path = agent["config"]()
    config_key: str = agent["config_key"]
    display = agent["config_display"]
    name = agent["name"]

    data = _read_config(config_path)
    if data is None:
        return False
    if not data or SERVER_NAME not in data.get(config_key, {}):
        print(f"  {DIM}{name}: {SERVER_NAME} not found in config — nothing to remove.{RESET}")
        return True

    del data[config_key][SERVER_NAME]
    _write_config(config_path, data, dry_run=dry_run)
    if not dry_run:
        print(f"  {GREEN}✓ {name} disconnected — {display}{RESET}")
    return True


def _check_venv(warn_only: bool = False) -> bool:
    if VENV_PYTHON.exists():
        return True
    msg = (
        f"{RED}Error: virtual environment not found at {VENV_PYTHON}{RESET}\n"
        f"Run {BOLD}bash install.sh{RESET} first to create it."
    )
    if warn_only:
        print(f"{RED}Warning: virtual environment not found.{RESET} "
              f"Run {BOLD}bash install.sh{RESET} first.")
        return False
    print(msg)
    return False


# ── Status command ────────────────────────────────────────────────────────────

def cmd_status() -> int:
    col_agent  = 18
    col_config = 44
    col_status = 15

    header_agent  = "Agent"
    header_config = "Config"
    header_status = "Status"

    print(f"\n{BOLD}{header_agent:<{col_agent}}{header_config:<{col_config}}{header_status}{RESET}")
    print("-" * (col_agent + col_config + col_status))

    for agent in AGENTS:
        config_path: Path = agent["config"]()
        display = agent["config_display"]
        name = agent["name"]

        data = _read_config(config_path)
        if data is None:
            status = f"{RED}✗ Invalid JSON{RESET}"
        elif not config_path.exists():
            status = f"{DIM}✗ Not found{RESET}"
        elif SERVER_NAME in data.get(agent["config_key"], {}):
            status = f"{GREEN}✓ Connected{RESET}"
        else:
            status = f"{RED}✗ Not connected{RESET}"

        # Truncate config display if too long
        disp = display if len(display) <= col_config - 2 else display[:col_config - 5] + "…"
        print(f"{name:<{col_agent}}{disp:<{col_config}}{status}")

    print()
    return 0


# ── Interactive mode ──────────────────────────────────────────────────────────

def cmd_interactive() -> int:
    print(f"\n{BOLD}⚡ Context Engine — Agent Connector{RESET}\n")

    _check_venv(warn_only=True)

    detected = [a for a in AGENTS if a["detected"]()]
    undetected = [a for a in AGENTS if not a["detected"]()]

    if not detected and not undetected:
        print("No agents found. Install a supported agent and try again.")
        return 0

    print("Detected agents:")
    for i, agent in enumerate(detected, 1):
        print(f"  [{i}] {agent['name']:<22} ({agent['config_display']})")

    if detected:
        print(f"\n  [a] Connect all detected")

    if undetected:
        print(f"\n{DIM}Not detected (can still be configured manually):{RESET}")
        for j, agent in enumerate(undetected, len(detected) + 1):
            print(f"  {DIM}[{j}] {agent['name']:<22} ({agent['config_display']}) — not detected{RESET}")

    print(f"\n  [q] Quit\n")

    all_agents = detected + undetected
    raw = input("Select agents (comma-separated, e.g. 1,3): ").strip().lower()

    if raw == "q" or raw == "":
        print("Bye.")
        return 0

    selected: list[dict] = []
    if raw == "a":
        selected = detected
    else:
        for token in raw.split(","):
            token = token.strip()
            if not token.isdigit():
                print(f"  Ignoring invalid selection: {token!r}")
                continue
            idx = int(token) - 1
            if 0 <= idx < len(all_agents):
                selected.append(all_agents[idx])
            else:
                print(f"  Ignoring out-of-range selection: {token}")

    if not selected:
        print("Nothing selected.")
        return 0

    if not _check_venv():
        return 1

    print()
    connected = 0
    for agent in selected:
        if _connect_agent(agent, dry_run=False, auto_overwrite=False):
            connected += 1

    print(f"\nDone! Connected to {connected} agent{'s' if connected != 1 else ''}.\n")
    _print_next_steps()
    return 0


def _print_next_steps() -> None:
    print("Next steps:")
    print(f"  1. Start the server:  .venv/bin/python3 server.py")
    print(f"  2. Restart your coding agents to pick up the new MCP config.")
    print()


# ── Non-interactive / flag-driven mode ───────────────────────────────────────

def cmd_noninteractive(args: argparse.Namespace) -> int:
    # Resolve selected agents
    selected: list[dict] = []

    if args.all:
        selected = list(AGENTS)
    else:
        for aid in ("claude_code", "cursor", "vscode", "windsurf", "claude_desktop"):
            flag = aid.replace("_", "-")
            if getattr(args, aid, False):
                agent = AGENT_BY_ID.get(flag)
                if agent:
                    selected.append(agent)

    if not selected:
        print("No agents selected. Use --all or specify agents (--claude-code, --cursor, etc.).")
        return 1

    if not args.dry_run and not args.remove:
        if not _check_venv():
            return 1

    print(f"\n{BOLD}⚡ Context Engine — Agent Connector{RESET}\n")

    errors = 0
    for agent in selected:
        if args.remove:
            ok = _disconnect_agent(agent, dry_run=args.dry_run)
        else:
            ok = _connect_agent(agent, dry_run=args.dry_run, auto_overwrite=True)
        if not ok:
            errors += 1

    if not args.dry_run and not args.remove:
        print()
        _print_next_steps()

    return 0 if errors == 0 else 1


# ── CLI ───────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="connect.py",
        description="Auto-configure MCP server connections for popular coding agents.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 connect.py                    # interactive mode
  python3 connect.py --all              # connect all known agents
  python3 connect.py --claude-code --cursor
  python3 connect.py --status           # check what's connected
  python3 connect.py --all --remove     # disconnect from all agents
  python3 connect.py --all --dry-run    # preview without writing files
""",
    )

    agent_group = parser.add_argument_group("agent selection (non-interactive)")
    agent_group.add_argument("--all", action="store_true", help="Select all known agents")
    agent_group.add_argument("--claude-code", dest="claude_code", action="store_true")
    agent_group.add_argument("--cursor", dest="cursor", action="store_true")
    agent_group.add_argument("--vscode", dest="vscode", action="store_true")
    agent_group.add_argument("--windsurf", dest="windsurf", action="store_true")
    agent_group.add_argument("--claude-desktop", dest="claude_desktop", action="store_true")

    op_group = parser.add_argument_group("operations")
    op_group.add_argument("--status", action="store_true", help="Report which agents have context-engine configured")
    op_group.add_argument("--remove", action="store_true", help="Remove context-engine from selected agents' configs")
    op_group.add_argument("--dry-run", dest="dry_run", action="store_true", help="Print what would be written without modifying files")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.status:
        return cmd_status()

    # Any agent flag or --all means non-interactive
    non_interactive = args.all or any([
        args.claude_code, args.cursor, args.vscode, args.windsurf, args.claude_desktop,
    ])

    if non_interactive or args.remove or args.dry_run:
        return cmd_noninteractive(args)

    return cmd_interactive()


if __name__ == "__main__":
    sys.exit(main())
