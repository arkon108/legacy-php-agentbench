"""Data models for the benchmark harness."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class Task(BaseModel):
    id: str
    repo: str
    base_commit: str
    fix_commit: str
    statement: str
    fail_to_pass: list[str]
    pass_to_pass: str
    gold_patch: str
    source_files: list[str]


class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any]


class ToolResult(BaseModel):
    name: str
    content: str
    success: bool = True


class Message(BaseModel):
    role: str
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None


class RunLimits(BaseModel):
    max_turns: int = 30
    max_tokens: int = 200_000
    timeout_seconds: int = 600


class ModelConfig(BaseModel):
    model_id: str
    temperature: float = 0.0


class ConfigA(BaseModel):
    """Single agent — one model runs the tool loop."""
    name: str = "A"
    worker: ModelConfig
    limits: RunLimits = RunLimits()


# Pricing in USD per 1M tokens (converted from JPY at ~150 JPY/USD)
# Source: platform.shisa.ai/ja/#pricing, retrieved 2026-11-04
PRICING: dict[str, dict[str, float]] = {
    "qwen3.7-flash": {"input": 0.033, "output": 0.133},
    "qwen3.7-plus": {"input": 0.40, "output": 1.60},
    "qwen3.7-max": {"input": 1.65, "output": 4.96},
    "glm-5.2": {"input": 1.40, "output": 4.40},
    "shisa-ai/shisa-v2.1-unphi4-14b": {"input": 0.20, "output": 0.40},
    "shisa-ai/shisa-v2.1-llama3.3-70b": {"input": 0.60, "output": 1.20},
    # Models with unknown pricing — estimate conservatively from similar tiers
    "qwen3.8-flash": {"input": 0.033, "output": 0.133},
    "qwen3.8-max": {"input": 1.65, "output": 4.96},
    "glm-5.3": {"input": 1.40, "output": 4.40},
    "deepseek-v4.1-flash-1017": {"input": 0.10, "output": 0.30},
    "kimi-k3": {"input": 0.60, "output": 1.80},
}


def compute_cost(model_id: str, tokens_in: int, tokens_out: int) -> float:
    prices = PRICING.get(model_id)
    if not prices:
        return 0.0
    return (tokens_in * prices["input"] + tokens_out * prices["output"]) / 1_000_000


class RunResult(BaseModel):
    task_id: str
    config: str
    run_number: int
    model_id: str
    resolved: bool = False
    fail_to_pass_passed: bool = False
    pass_to_pass_passed: bool = False
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    wall_time_seconds: float = 0.0
    turns: int = 0
    diff: str = ""
    error: str | None = None
