---
name: server
description: Start, stop, restart or check the ClaimLens FastAPI backend (uvicorn on port 8000) running in the background. Use when the user asks to start, run, launch, stop, kill, restart or check the server or backend, or invokes /server.
---

# Run the ClaimLens backend

All work goes through `scripts/server.ps1`. Do not type raw `uvicorn` commands, and do not kill processes by name.

## Commands

Run from the project root, in PowerShell. Pick the action the user asked for: `start`, `stop`, `status` or `restart`.

```
powershell -ExecutionPolicy Bypass -NoProfile -File scripts\server.ps1 start
powershell -ExecutionPolicy Bypass -NoProfile -File scripts\server.ps1 stop
powershell -ExecutionPolicy Bypass -NoProfile -File scripts\server.ps1 status
powershell -ExecutionPolicy Bypass -NoProfile -File scripts\server.ps1 restart
```

An optional `-Port 8001` argument changes the port. Use the default unless the user asks otherwise.

## Steps

1. If the user asked to start, run `start`. If the output says the port is already in use, run `status` and tell the user what is running. Do not stop something you did not start unless the user asks.
2. If the user asked to stop, run `stop`. Report the script's message as is.
3. If the user asked to check, run `status`.
4. After `start` or `restart`, report the URL (`http://localhost:8000`), the docs URL (`/docs`) and the log path, `data/server.log`.
5. If start fails, the script prints the last lines of `data/server.err.log`. Show those lines and stop. Common causes: missing `.venv` (see README), a missing `ANTHROPIC_API_KEY` or `MOCK_MODE` in `.env`, or a port clash.

## Rules

- The server runs hidden in the background. It keeps running after this session ends until someone runs `stop`.
- The script only stops a process on the port if its command line is `uvicorn ... app.main:app`. If something else holds the port, it reports that and leaves it alone.
- Do not add `--reload` to this flow. The script needs the process it starts to be the one listening.
- Logs, the PID file and the database are under `data/`, which git ignores.
