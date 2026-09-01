"""A scripted Strands model, for exercising the agent loop without Bedrock.

This is test scaffolding, never imported by the agent or the API. Its purpose is
narrow and worth stating precisely, because it would be easy to mistake for a
mock of the thing that matters:

  It verifies that when the model asks for a tool, everything downstream works —
  the tool runs, evidence is archived, the verdict is recorded and validated,
  the packet renders. It does NOT verify the model's judgment, which is the
  actual product and cannot be faked.

So a passing golden-run test means "the machinery around the model is sound".
The agent's tool *selection* stays UNVERIFIED until Bedrock access is granted.

Emits the Bedrock converse-stream event shape Strands expects.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterable
from typing import Any

from strands.models import Model


class ScriptedModel(Model):
    """Replays a fixed list of turns.

    Each turn is either:
        {"tools": [{"name": ..., "input": {...}}, ...]}   one or more tool calls
        {"text": "..."}                                    a final answer

    A tool input may be a callable taking no arguments, resolved at call time.
    A real model reads an id out of a previous tool result before citing it; a
    scripted one cannot, so this is how a turn refers to something an earlier
    turn produced.

    Records every tool_spec it was offered, so a test can assert the agent was
    actually given the tools it should have been.
    """

    def __init__(self, turns: list[dict]):
        self._turns = list(turns)
        self._i = 0
        self.seen_tool_specs: list[str] = []
        self.seen_system_prompt: str | None = None
        self.calls = 0

    # -- Model interface --------------------------------------------------

    def get_config(self) -> dict:
        return {"model_id": "scripted-test-model"}

    def update_config(self, **kwargs: Any) -> None:
        pass

    async def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):
        raise NotImplementedError("ScriptedModel does not support structured output")

    async def stream(
        self,
        messages,
        tool_specs=None,
        system_prompt=None,
        **kwargs: Any,
    ) -> AsyncIterable[dict]:
        self.calls += 1
        if tool_specs:
            self.seen_tool_specs = [t["name"] for t in tool_specs]
        if system_prompt:
            self.seen_system_prompt = system_prompt

        if self._i >= len(self._turns):
            # Script exhausted: end the conversation rather than looping.
            async for e in self._text("Done."):
                yield e
            return

        turn = self._turns[self._i]
        self._i += 1

        if "text" in turn:
            async for e in self._text(turn["text"]):
                yield e
        else:
            async for e in self._tool_calls(turn["tools"]):
                yield e

    # -- event emitters ---------------------------------------------------

    async def _text(self, text: str):
        yield {"messageStart": {"role": "assistant"}}
        yield {"contentBlockDelta": {"delta": {"text": text}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "end_turn"}}
        yield self._usage()

    async def _tool_calls(self, tools: list[dict]):
        yield {"messageStart": {"role": "assistant"}}
        for i, t in enumerate(tools):
            yield {
                "contentBlockStart": {
                    "start": {
                        "toolUse": {
                            "name": t["name"],
                            "toolUseId": f"tu-{self._i}-{i}",
                        }
                    }
                }
            }
            payload = t.get("input", {})
            if callable(payload):
                payload = payload()
            yield {
                "contentBlockDelta": {
                    "delta": {"toolUse": {"input": json.dumps(payload)}}
                }
            }
            yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "tool_use"}}
        yield self._usage()

    @staticmethod
    def _usage() -> dict:
        return {
            "metadata": {
                "usage": {"inputTokens": 10, "outputTokens": 10, "totalTokens": 20},
                "metrics": {"latencyMs": 1},
            }
        }
