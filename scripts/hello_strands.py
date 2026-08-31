"""Phase 0 go/no-go gate.

A minimal Strands agent with ONE real tool. The agent must autonomously decide to
call the tool and answer from its output. If this does not work, nothing
downstream matters.

Run:  ./.venv/bin/python scripts/hello_strands.py
"""

import os

import boto3
from strands import Agent, tool
from strands.models import BedrockModel

REGION = os.environ.get("AWS_REGION", "us-east-1")
MODEL_ID = os.environ.get(
    "BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
)

_called = {"count": 0}


@tool
def get_caller_identity() -> dict:
    """Return the AWS identity and region these credentials resolve to."""
    _called["count"] += 1
    sts = boto3.client("sts", region_name=REGION)
    ident = sts.get_caller_identity()
    return {
        "arn": ident["Arn"],
        "user_id": ident["UserId"],
        "account": ident["Account"],
        "region": REGION,
    }


def main() -> int:
    agent = Agent(
        model=BedrockModel(model_id=MODEL_ID, region_name=REGION),
        tools=[get_caller_identity],
        system_prompt=(
            "You are a precise AWS assistant. Use the tools available to answer "
            "questions about the current AWS environment. Never guess: if a tool "
            "can answer the question, call it and answer only from its output."
        ),
    )

    result = agent("Who am I in AWS, and what region am I operating in?")

    print("\n" + "=" * 60)
    print(f"tool invocations: {_called['count']}")
    if _called["count"] == 0:
        print("GATE FAILED: the agent answered without calling the tool.")
        return 1
    print("GATE PASSED: agent autonomously selected and called the real tool.")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
