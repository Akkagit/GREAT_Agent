"""Compatibility import for callers using the former HTTP provider name."""

from great_agent.adapters.agentscope_model import AgentScopeModelProvider

OpenAICompatibleProvider = AgentScopeModelProvider

__all__ = ["AgentScopeModelProvider", "OpenAICompatibleProvider"]
