import pytest

from helpdesk import part1


@pytest.mark.parametrize("msg,expected", [
    ("I cannot connect to Wi-Fi.", "wifi_help"),
    ("my WIFI is slow", "wifi_help"),
    ("I forgot my PASSWORD.", "password_help"),
    ("My laptop screen is broken.", "other_help"),
    ("My Wi-Fi password does not work.", "wifi_help"),   # original graph: wifi rule wins
])
def test_original_routing(msg, expected):
    res = part1.run(msg, "original")
    assert res["nodes"] == ["read_request", expected]
    assert res["steps"][-1]["state_after"]["message"] == msg   # message is preserved


def test_both_topics_go_to_clarify_after_change():
    res = part1.run("My Wi-Fi password does not work.", "with_clarification")
    assert res["nodes"] == ["read_request", "clarify_problem"]
    assert "Wi-Fi" in res["reply"] and "password" in res["reply"]


def test_clarify_does_not_change_single_topic_routes():
    assert part1.run("I forgot my password", "with_clarification")["nodes"][-1] == "password_help"
    assert part1.run("wifi down", "with_clarification")["nodes"][-1] == "wifi_help"


def test_help_nodes_return_text_from_helpdesk_json():
    assert "GUIDE-WIFI" in part1.run("wifi", "original")["reply"]
    assert "GUIDE-PASSWORD" in part1.run("password", "original")["reply"]
