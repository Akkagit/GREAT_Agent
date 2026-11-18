# P0 isolated compatibility validation

These fixtures do not import the product provider, start the API/worker, read research
data, open a database, or invoke GREAT. Running a fixture leaves the product Python
and frontend dependency manifests untouched. All environments, builds and JSON
outputs belong in `tmp/`. The subsequent product SDK/antd cutover is documented in
[`2026-11-02-validated-cutover.md`](../../docs/validation/2026-11-02-validated-cutover.md).

Run from the repository root with Python 3.11, uv, Node 24 and npm 11:

```powershell
$env:UV_CACHE_DIR = "$PWD/tmp/p0-uv-cache"
$env:UV_PROJECT_ENVIRONMENT = "$PWD/tmp/p0-python"
uv sync --project scripts/compatibility/python --python .venv/Scripts/python.exe --locked --index-url https://pypi.org/simple
& tmp/p0-python/Scripts/python.exe scripts/compatibility/probe.py --output tmp/p0-offline.json
& .venv/Scripts/python.exe -m pytest backend/tests/test_compatibility_probe.py -q
```

The Python fixture pins AgentScope **2.0.11** and locks all transitive dependencies.
It refuses to label another version compatible. Default mode exclusively uses
`httpx.MockTransport`; all messages, tools, HTTP errors, SSE chunks and usage are
synthetic. A successful offline matrix proves SDK wire/parse behavior, **not provider
or product integration**. The mock matrix also exposes structured-output fallback
and cancellation semantics.

On Windows the sandbox can stall at `socket.socketpair` during asyncio initialization.
Run with approved loopback permissions; do not rewrite the SDK to conceal that issue.
For a bounded diagnostic use Python's `faulthandler.dump_traceback_later(20)` before
`runpy.run_path(...)`. Initial imports may also be slow on a cold Windows environment.

Live mode is explicit:

```powershell
# Only use the already authorized process environment key; never paste it into logs.
& tmp/p0-python/Scripts/python.exe scripts/compatibility/probe.py --live --config config/local.toml --output tmp/p0-live.json
```

It reads only `AppSettings.load(config, os.environ)` for provider configuration. The
existing `GREAT_AGENT_MODEL_API_KEY` must be present in this process; otherwise live
is `skipped` with zero requests. No fallback credential stores are searched. Output
contains safe status/count/type metadata; errors, raw responses, keys, configured
paths and provider URLs are not printed. SDK logging is disabled before calls.

Live sends at most three planned synthetic checks: native streaming forced tools,
vendor `max_tokens` streaming forced tools, and JSON-object baseline. There are no
tool executions. SDK and OpenAI retries are both zero. A transport guard counts
every actual attempt, refuses the ninth attempt, checks every outgoing output cap
is present and <=512, and enforces one shared 180-second deadline across connection,
response headers and response stream. No automatic live fallback strategy is used.
Inspect interrupted terminal responses as failures even when cancellation was
swallowed by the SDK. Package unavailable, failed offline prerequisites, missing
key and live HTTP failures are distinct results. Exit 1 means the requested mode
was not fully verified, including an explicitly requested live run that was skipped.

Two separately locked Node fixtures test antd alone and antd plus optional X:

```powershell
foreach ($fixture in @('node-antd-only', 'node')) {
    $destination = "tmp/p0-$fixture-reproduce"
    New-Item -ItemType Directory -Force $destination | Out-Null
    Copy-Item "scripts/compatibility/$fixture/*" $destination -Recurse -Force
    npm ci --prefix $destination --ignore-scripts --no-audit --no-fund --registry=https://registry.npmjs.org
    npm ls --prefix $destination react react-dom antd --depth=0
    npm run --prefix $destination typecheck
    npm run --prefix $destination build
    # Extra declaration audit; expected upstream failures are documented separately.
    npm run --prefix $destination typecheck:strict-declarations
}
```

Fixture typechecking uses the product's bundler module resolution, automatic React
JSX and `skipLibCheck=true`, with additional `strict=true`. The extra declaration
audit sets `skipLibCheck=false`. Passing the normal gate does not erase upstream
declaration errors. Builds are production bundling checks; browser interaction,
accessibility and migration of the product UI are outside this spike.

See `docs/validation/2026-11-02-p0-compatibility.md` for actual versions, results,
limitations and the switching decision.
