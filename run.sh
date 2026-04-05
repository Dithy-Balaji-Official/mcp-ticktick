#!/bin/bash
cd /Users/dithai/.openclaw/mcp-servers/karbassi-ticktick-mcp
set -a; source .env; set +a
exec uv run mcp-ticktick
