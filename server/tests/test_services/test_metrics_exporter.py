from app.api.metrics_exporter import render_metrics


def test_render_metrics_exposition():
    text = render_metrics({"online": 3, "offline": 1}, {"critical": 2}, 3)
    assert 'dtsys_devices{status="online"} 3' in text
    assert 'dtsys_devices{status="offline"} 1' in text
    assert 'dtsys_alerts_unresolved{severity="critical"} 2' in text
    assert "dtsys_websocket_connections 3" in text
    assert text.endswith("\n")


def test_render_metrics_escapes_labels():
    text = render_metrics({'we"ird': 1}, {}, 0)
    assert 'status="we\\"ird"' in text
