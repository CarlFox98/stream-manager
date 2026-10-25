# Stream Manager v0.14.1

**Clearer MCP server setup.** Docs and one error message; no behaviour change
for the app or the MCP tools.

- The README now says `python -m pip install -r requirements-mcp.txt`. With
  more than one Python on a PC, bare `pip` can install the SDK into a different
  Python than `mcp-server.bat` runs. The server then quits on every start, and
  Claude only reports "Connection closed".
- When the SDK is missing, the server now prints the exact command to fix it,
  using the full path of the Python it is running under.
- New **If it doesn't connect** note in the README: where Claude logs server
  starts, and that Claude stops retrying after three failed starts.

## Tests

- 239 pass, unchanged in number. The missing-SDK test now also checks that
  the message names the running interpreter.
