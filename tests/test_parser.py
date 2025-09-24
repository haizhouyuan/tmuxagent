from tmux_agent import parser


def test_parse_json_messages():
    lines = [
        "### SENTRY {\"type\": \"STATUS\", \"stage\": \"lint\", \"ok\": true}",
        "### SENTRY {\"type\": \"ASK\", \"stage\": \"build\"}",
        "Something passed",
        "An ERROR occurred",
    ]
    msgs = parser.parse_messages(lines)
    kinds = [m.kind for m in msgs]
    assert kinds == ["STATUS", "ASK", "STATUS", "ERROR"]
    assert msgs[0].payload["stage"] == "lint"
