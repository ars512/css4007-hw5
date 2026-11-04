import json

from helpdesk import tools


def test_find_instructions_wifi_and_password_and_unknown():
    assert tools.find_instructions("wifi")["instruction_id"] == "GUIDE-WIFI"
    assert tools.find_instructions("Wi-Fi")["found"]
    assert tools.find_instructions("password")["instruction_id"] == "GUIDE-PASSWORD"
    assert tools.find_instructions("printer")["found"] is False


def test_check_service_status_profiles():
    assert tools.check_service_status("wifi", "normal")["status"] == "operational"
    assert tools.check_service_status("wifi", "wifi_outage")["status"] == "outage"
    assert tools.check_service_status("account_recovery", "recovery_outage")["status"] == "outage"
    assert tools.check_service_status("wifi", "status_unavailable")["status"] == "unknown"
    assert tools.check_service_status("printer", "normal")["status"] == "unknown"   # never 'operational'


def test_prepare_ticket_writes_local_draft(tmp_path):
    r = tools.prepare_ticket("Wi-Fi fails", ["enabled Wi-Fi"], category="wifi", user_id="demo-user-a",
                             thread_id="t", status_profile="normal", tickets_dir=str(tmp_path))
    assert r["ok"] and r["sent"] is False
    data = json.loads((tmp_path / r["file"]).read_text(encoding="utf-8"))
    assert data["status"] == "draft" and data["steps_tried"] == ["enabled Wi-Fi"]
    assert data["service_status"] == "not checked" and data["ticket_id"].startswith("TKT-")


def test_prepare_ticket_refuses_missing_details(tmp_path):
    r = tools.prepare_ticket("", [], tickets_dir=str(tmp_path))
    assert r["ok"] is False and set(r["missing"]) == {"problem description", "steps already tried"}
    assert list(tmp_path.iterdir()) == []
