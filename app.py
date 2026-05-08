from __future__ import annotations

import io
import threading
import uuid
from typing import Any, Dict

from flask import Flask, jsonify, render_template, request, send_file

from database import ScanDatabase
from exporter import export_pdf
from scanner import ScanStopped, ScanTimedOut, ScanWorker, check_nmap_installed


app = Flask(__name__, template_folder="templates")
db = ScanDatabase()

# Active scans store: scan_id -> state
active_scans: Dict[str, Dict[str, Any]] = {}
active_lock = threading.Lock()


@app.get("/")
def index() -> Any:
    return render_template("index.html")


def _drain_output(state: Dict[str, Any]) -> list[str]:
    with state["lock"]:
        out = list(state["output"])
        state["output"].clear()
        return out


def _dynamic_timeout_s(profile: str, target: str, ports_arg: str) -> int:
    """
    Dynamic scan timeout to avoid failing slower profiles / large subnets.
    Does NOT change command generation; it only changes the watchdog limit.
    """
    p = (profile or "").strip()
    t = (target or "").strip()
    ports = (ports_arg or "").strip()

    # baseline (1 hour) to avoid slow-profile failures
    timeout = 3600

    # slow profiles / heavy operations
    slow_tokens = ("-T0", "-T1", "-T2", "-sU", "-sS -T2", "-p-")
    if any(tok in p for tok in slow_tokens):
        timeout = max(timeout, 3600)  # 1 hour

    # full port range is heavy regardless of profile text
    if "-p 1-65535" in ports or "-p-" in p:
        timeout = max(timeout, 3600)

    # large target sets: ranges or CIDR
    if "/" in t:
        # heuristic: bigger networks get bigger ceilings
        if t.endswith("/24"):
            timeout = max(timeout, 1800)
        elif t.endswith("/23") or t.endswith("/22"):
            timeout = max(timeout, 2700)
        else:
            timeout = max(timeout, 3600)
    if "-" in t:
        timeout = max(timeout, 1800)

    # never lower than 3600s, never ridiculously high by default
    return max(3600, min(timeout, 6 * 3600))


def run_scan_thread(scan_id: str, payload: Dict[str, Any]) -> None:
    with active_lock:
        state = active_scans.get(scan_id)
    if not state:
        return

    target = str(payload.get("target", "")).strip()
    profile = str(payload.get("profile", "-T4 -F")).strip() or "-T4 -F"
    ports = str(payload.get("ports", "")).strip()
    os_detect = bool(payload.get("os_detect", False))
    svc_version = bool(payload.get("svc_version", False))
    timeout_s = _dynamic_timeout_s(profile, target, ports)

    def emit(line: str) -> None:
        with state["lock"]:
            state["output"].append(line)
            state["output_history"].append(line)

    worker = ScanWorker(
        target=target,
        base_args=profile,
        ports_arg=ports,
        os_detect=os_detect,
        svc_version=svc_version,
        timeout_s=timeout_s,
        stop_event=state["stop_flag"],
        output_callback=emit,
    )

    state["worker"] = worker
    try:
        emit(f"Starting scan on target: {target}")
        emit(f"Profile args: {profile}  Ports arg: {ports or 'default'}  OS: {os_detect}  SVC: {svc_version}")
        emit(f"Timeout: {timeout_s}s (Stop Scan can cancel any time)")
        emit("-" * 60)

        results, hosts_up, open_ports, summary = worker.run()
        with state["lock"]:
            state["results"] = results
            state["hosts_up"] = hosts_up
            state["open_ports"] = open_ports
            state["summary"] = summary
            if state["status"] != "stopped":
                state["status"] = "done"

        emit("-" * 60)
        emit(summary)

        if state["status"] == "done":
            db.save_scan(
                target=target,
                profile=profile,
                ports=ports,
                result_summary=summary,
                full_output="\n".join(state["output_history"]),
            )
    except ScanStopped:
        with state["lock"]:
            state["status"] = "stopped"
        emit("Scan stopped by user.")
    except ScanTimedOut as exc:
        with state["lock"]:
            if state["status"] != "stopped":
                state["status"] = "error"
                state["error"] = str(exc)
        emit(str(exc))
    except Exception as exc:
        with state["lock"]:
            if state["status"] != "stopped":
                state["status"] = "error"
                state["error"] = str(exc)
        emit(f"Error: {exc}")


@app.post("/api/scan/start")
def start_scan() -> Any:
    payload = request.get_json(silent=True) or {}
    target = str(payload.get("target", "")).strip()
    if not target:
        return jsonify({"error": "Target is required"}), 400

    if not check_nmap_installed():
        return jsonify({"error": "Nmap is not installed or not in PATH. Download from nmap.org"}), 500

    scan_id = str(uuid.uuid4())[:8]
    state: Dict[str, Any] = {
        "status": "running",
        "output": [],
        "results": [],
        "hosts_up": 0,
        "open_ports": 0,
        "summary": "",
        "error": "",
        "stop_flag": threading.Event(),
        "lock": threading.Lock(),
        "worker": None,
        # for saving history reliably
        "output_history": [],
    }

    with active_lock:
        active_scans[scan_id] = state

    threading.Thread(target=run_scan_thread, args=(scan_id, payload), daemon=True).start()

    return jsonify({"scan_id": scan_id})


@app.get("/api/scan/status/<scan_id>")
def scan_status(scan_id: str) -> Any:
    with active_lock:
        state = active_scans.get(scan_id)
    if not state:
        return jsonify({"error": "Scan not found"}), 404

    output = _drain_output(state)
    return jsonify(
        {
            "status": state["status"],
            "output": output,
            "results": state.get("results", []),
            "hosts_up": state.get("hosts_up", 0),
            "open_ports": state.get("open_ports", 0),
            "error": state.get("error", ""),
        }
    )


@app.post("/api/scan/stop/<scan_id>")
def stop_scan(scan_id: str) -> Any:
    with active_lock:
        state = active_scans.get(scan_id)
    if state:
        with state["lock"]:
            state["status"] = "stopped"
        state["stop_flag"].set()
        try:
            worker = state.get("worker")
            if worker:
                worker.stop()
        except Exception:
            pass
    return jsonify({"ok": True})


@app.post("/api/export/pdf")
def export_pdf_route() -> Any:
    payload = request.get_json(silent=True) or {}
    results = list(payload.get("results", []))
    if not results:
        return jsonify({"error": "No results to export."}), 400

    buf = io.BytesIO()
    export_pdf(results, "NmapX Scan", "", buf)
    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/pdf",
        as_attachment=True,
        download_name="NmapX_Report.pdf",
    )


@app.get("/api/history")
def get_history() -> Any:
    return jsonify({"scans": db.get_all_scans()})


@app.post("/api/history/clear")
def clear_history() -> Any:
    db.clear_all()
    return jsonify({"ok": True})


if __name__ == "__main__":
    if not check_nmap_installed():
        print("WARNING: Nmap is not installed or not in PATH.")
        print("Download from: https://nmap.org/download.html")
    app.run(debug=False, port=5000)

