from pathlib import Path

from tmux_agent.orchestrator.decomposer import decompose_requirements


def test_decompose_weather_doc():
    doc_path = Path("docs/weather_bot_end_to_end_plan.md")
    steps = decompose_requirements(doc_path)
    assert steps, "expected steps from requirements doc"
    file_targets = [step["target"] for step in steps if step.get("type") == "create_file"]
    assert "scripts/weather_api_stub.py" in file_targets
    commands = [step["command"] for step in steps if step.get("type") == "run_command"]
    assert "mkdir -p /home/yuanhaizhou/projects/testprojectfortmuxagent" in commands
