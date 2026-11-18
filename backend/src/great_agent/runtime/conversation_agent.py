"""Bounded native AgentScope 2.0 conversation runtime.

Domain writes belong to the server-bound callback, never to the model or SDK.
"""
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import time

import httpx
from agentscope.agent import Agent, ContextConfig, InjectionConfig, ModelConfig, ReActConfig
from agentscope.credential import OpenAICredential
from agentscope.message import Msg, TextBlock
from agentscope.middleware import MiddlewareBase
from agentscope.model import OpenAIChatModel
from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.skill import LocalSkillLoader
from agentscope.state import AgentState
from agentscope.tool import FunctionTool, Toolkit

from great_agent.adapters.agentscope_model import _ClientTransport
from great_agent.adapters.model_provider import ModelProviderError, ModelTimeout
from great_agent.config import ModelSettings


@dataclass
class AgentTurnResult:
    text: str
    proposal: dict | None
    state: dict
    metrics: dict


class _Budget(MiddlewareBase):
    def __init__(self, requests, tools, seconds):
        self.request_limit = requests
        self.tool_limit = tools
        self.ends = time.monotonic() + seconds
        self.requests = 0
        self.tools = 0
        self.error = None

    def check(self):
        if time.monotonic() >= self.ends:
            self.error = ModelTimeout("model request timed out")
        task = asyncio.current_task()
        if task and task.cancelling():
            raise asyncio.CancelledError
        if self.error:
            raise self.error

    async def on_acting(self, agent, input_kwargs, next_handler):
        self.check()
        if self.tools >= self.tool_limit:
            self.error = ModelProviderError("tool call limit exceeded")
            raise self.error
        self.tools += 1
        async for item in next_handler(**input_kwargs):
            yield item
        self.check()

    async def on_compress_context(self, agent, input_kwargs, next_handler):
        # History is bounded before Agent construction. No implicit summary LLM.
        self.check()


class _TurnTransport(httpx.AsyncBaseTransport):
    def __init__(self, upstream, budget):
        self.upstream, self.budget = upstream, budget

    async def handle_async_request(self, request):
        b = self.budget
        b.check()
        if b.requests >= b.request_limit:
            b.error = ModelProviderError("model request limit exceeded")
            raise b.error
        b.requests += 1
        response = await self.upstream.handle_async_request(request)
        b.check()
        if not response.is_success:
            b.error = ModelProviderError("model provider request failed")
            b.error.upstream_status = response.status_code
            await response.aclose()
            raise b.error
        await response.aread()
        try:
            choice = response.json()["choices"][0]
            if choice["finish_reason"] not in ("stop", "tool_calls") or choice["message"].get("refusal"):
                raise ValueError
        except (ValueError, KeyError, IndexError, TypeError):
            b.error = ModelProviderError("model response was refused or incomplete")
            await response.aclose()
            raise b.error
        return response


def _normalized(path):
    return path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").strip() + "\n"


def _restore(state, history):
    # Restore public AgentState, but retain text conversation only. In particular,
    # old tool results (plan snapshots), permissions and task caches cannot survive.
    restored = False
    entries = history
    if isinstance(state, dict) and state.get("version") == 1:
        try:
            saved = AgentState.model_validate(state["agent"])
            entries = [{"role": m.role, "text": "".join(b.text for b in m.content if isinstance(b, TextBlock))}
                       for m in saved.context if m.role in ("user", "assistant")]
            restored = True
        except (ValueError, KeyError, TypeError):
            pass
    clean = AgentState()
    for item in entries[-24:]:
        if item.get("role") in ("user", "assistant") and isinstance(item.get("text"), str):
            clean.context.append(Msg(name=item["role"], role=item["role"], content=[TextBlock(text=item["text"][:8000])]))
    return clean, restored


class AgentScopeConversationEngine:
    def __init__(self, settings: ModelSettings, *, resource_root: Path,
                 max_model_requests=4, max_tool_calls=6, timeout_seconds=90, transport=None):
        self.settings = settings
        self.resource_root = Path(resource_root)
        self.max_model_requests = max_model_requests
        self.max_tool_calls = max_tool_calls
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        if min(max_model_requests, max_tool_calls, timeout_seconds) <= 0:
            raise ValueError("conversation budgets must be positive")

    async def reply(self, message, *, history, context, state, propose, create=None, creation_defaults=None):
        if not self.settings.api_key.get_secret_value().strip():
            error = ModelProviderError("model credentials are not configured")
            error.diagnostic_code = "missing_api_key"
            raise error
        budget = _Budget(self.max_model_requests, self.max_tool_calls, self.timeout_seconds)
        proposal = None
        attempted = False
        creation_attempted = False
        creation_enabled = callable(create)
        root = self.resource_root
        source = "configured"
        if not (root / "SYSTEM.md").is_file():
            root = Path(__file__).parents[1] / "resources" / "agent"
            source = "bundled"
        try:
            async with asyncio.timeout(self.timeout_seconds) as deadline:
                policy = _normalized(root / "SYSTEM.md")
                loader = LocalSkillLoader(str(root / "skills"), scan_subdir=True)
                skills = await loader.list_skills()
                versions = {s.name: sha256(_normalized(Path(s.dir) / "SKILL.md").encode()).hexdigest() for s in skills}
                def result(data):
                    # FunctionTool accepts strings, JSON values or ToolChunk;
                    # ToolResponse is its accumulated output, not a function
                    # return type, and otherwise gets stringified as an SDK repr.
                    return json.dumps(data, ensure_ascii=False)

                async def get_capabilities():
                    """Read current server capabilities and limitations."""
                    return result({"conversation": True, "knowledge_search": False, "paper_analysis_ready": False,
                                   "proposal_enabled": context is not None and not context.get("read_only", True),
                                   "experiment_creation": creation_enabled,
                                   "execution": False,
                                   "chat_execution_confirmation": bool((context or {}).get("execution", {}).get("chat_confirmation_supported"))})

                async def get_current_plan():
                    """Read the server-bound current experiment plan."""
                    return result(context or {"bound": False})

                async def get_task_status():
                    """Read task status from the server-bound experiment snapshot."""
                    return result({"tasks": (context or {}).get("tasks", [])})

                async def get_experiment_defaults():
                    """Read the complete server default ExperimentSpec baseline, not a user-selected or existing plan."""
                    return result(creation_defaults or {})

                async def create_experiment(candidate: dict):
                    """Validate and stage one complete new draft ExperimentSpec. Only a later server receipt proves creation; never execute."""
                    nonlocal creation_attempted
                    budget.check()
                    if creation_attempted:
                        return result({"error": "Only one experiment creation attempt is allowed per turn."})
                    creation_attempted = True
                    try:
                        outcome = await create(candidate)
                        budget.check()
                        return result(outcome)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        budget.error = ModelProviderError("experiment creation validation failed")
                        raise budget.error from None

                async def propose_plan_change(candidate: dict, explanation: str):
                    """Validate and save one complete candidate proposal for user review. Never execute it."""
                    nonlocal attempted, proposal
                    budget.check()
                    if attempted:
                        return result({"error": "Only one proposal attempt is allowed per turn."})
                    attempted = True
                    try:
                        outcome = await propose(candidate, explanation)
                        budget.check()
                        proposal = outcome.get("proposal") if not outcome.get("no_change") else None
                        return result({"proposal": proposal, "no_change": bool(outcome.get("no_change"))})
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        budget.error = ModelProviderError("plan proposal validation failed")
                        raise budget.error from None

                allow = PermissionDecision(behavior=PermissionBehavior.ALLOW, message="Server-authorized bounded tool")
                tools = [FunctionTool(f, is_read_only=True, permission=allow) for f in (get_capabilities, get_current_plan, get_task_status)]
                if context is not None and not context.get("read_only", True):
                    tools.append(FunctionTool(propose_plan_change, is_concurrency_safe=False, permission=allow))
                if creation_enabled:
                    tools.append(FunctionTool(get_experiment_defaults, is_read_only=True, permission=allow))
                    tools.append(FunctionTool(create_experiment, is_concurrency_safe=False, permission=allow))
                # Native Skill resolves names only against this trusted loader;
                # its calls pass through the same acting budget as domain tools.
                toolkit = Toolkit(tools=tools, skills_or_loaders=[loader])
                prompt = policy
                prompt += "\nCurrent server binding: " + json.dumps(context or {"bound": False}, ensure_ascii=False)
                prompt += "\nCurrent server capabilities: " + json.dumps({"experiment_creation": creation_enabled})
                sdk_state, restored = _restore(state, history)
                timeout = httpx.Timeout(connect=self.settings.connect_timeout_seconds, read=self.settings.read_timeout_seconds,
                                        write=self.settings.read_timeout_seconds, pool=self.settings.connect_timeout_seconds)
                async with AsyncExitStack() as clients:
                    upstream = await clients.enter_async_context(httpx.AsyncClient(transport=self.transport, timeout=timeout, follow_redirects=False))
                    client = await clients.enter_async_context(httpx.AsyncClient(transport=_TurnTransport(_ClientTransport(upstream), budget), timeout=timeout, follow_redirects=False))
                    extra = {"max_tokens": self.settings.max_tokens}
                    if self.settings.name.lower().startswith("glm"):
                        extra["thinking"] = {"type": "disabled"}
                    model = OpenAIChatModel(credential=OpenAICredential(api_key=self.settings.api_key, base_url=str(self.settings.base_url)),
                        model=self.settings.name, parameters=OpenAIChatModel.Parameters(max_tokens=None), stream=False, max_retries=0,
                        client_kwargs={"http_client": client, "max_retries": 0, "timeout": timeout}, extra_body=extra)
                    agent = Agent("GREAT", prompt, model, toolkit=toolkit, state=sdk_state, middlewares=[budget],
                        model_config=ModelConfig(max_retries=0), context_config=ContextConfig(compression_tool_enabled=False),
                        injection_config=InjectionConfig(inject_runtime_state=False),
                        react_config=ReActConfig(max_iters=self.max_model_requests, interruption_raise_cancelled_error=True))
                    answer = await agent.reply(Msg(name="user", role="user", content=[TextBlock(text=message)]))
                    if deadline.expired():
                        raise ModelTimeout("model request timed out")
                    budget.check()
                    text = "".join(b.text for b in answer.content if isinstance(b, TextBlock)).strip()
                    if not text:
                        raise ModelProviderError("model returned no answer")
                    # An iteration-limit receipt must not masquerade as a normal answer.
                    if budget.requests >= self.max_model_requests and any(getattr(b, "type", "") == "tool_call" for b in answer.content):
                        raise ModelProviderError("model request limit exceeded")
                    clean, _ = _restore({"version": 1, "agent": agent.state.model_dump(mode="json")}, [])
                    return AgentTurnResult(text, proposal, {"version": 1, "agent": clean.model_dump(mode="json")},
                        {"model_requests": budget.requests, "physical_model_requests": budget.requests,
                         "tool_calls": budget.tools, "state_restored": restored,
                         "elapsed_ms": round((time.monotonic() - budget.ends + self.timeout_seconds) * 1000, 2),
                         "policy_hash": sha256(policy.encode()).hexdigest(), "skill_hashes": versions,
                         "policy_version": sha256(policy.encode()).hexdigest(), "skill_versions": versions, "resource_source": source})
        except asyncio.CancelledError:
            raise
        except (TimeoutError, httpx.TimeoutException):
            raise ModelTimeout("model request timed out") from None
        except Exception:
            if time.monotonic() >= budget.ends:
                raise ModelTimeout("model request timed out") from None
            if budget.error:
                raise budget.error from None
            raise ModelProviderError("conversation model request failed") from None
