from __future__ import annotations

import ctypes
import os
import tempfile
import subprocess
import time
from typing import Callable, Dict, List, Optional, Tuple

import nmap

OutputCallback = Callable[[str], None]


class ScanStopped(Exception):
    pass


class ScanTimedOut(Exception):
    pass


def check_nmap_installed() -> bool:
    try:
        subprocess.run(
            ["nmap", "--version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        return True
    except Exception:
        return False


def check_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _normalize_args(base_args: str, ports_arg: str, os_detect: bool, svc_version: bool) -> Tuple[str, Optional[str]]:
    args = (base_args or "-T4 -F").strip()

    # Performance: enforce fast quick-scan preset when user picked "-T4 -F"
    if args.strip() == "-T4 -F" and "--max-retries" not in args:
        args = f"{args} --max-retries 1"

    if os_detect and "-O" not in args:
        args = f"{args} -O"
    if svc_version and "-sV" not in args:
        args = f"{args} -sV"

    ports_arg = (ports_arg or "").strip()
    ports: Optional[str] = None

    # JS sends ports_arg like "--top-ports 1000" or "-p 1-65535"
    if ports_arg.startswith("-p "):
        ports = ports_arg.replace("-p", "", 1).strip()
    elif ports_arg:
        args = f"{args} {ports_arg}"

    return args.strip(), ports


def _scan_to_rows(nm: nmap.PortScanner) -> Tuple[List[Dict], int, int]:
    results: List[Dict] = []
    hosts_up = 0
    open_ports = 0

    for host in nm.all_hosts():
        try:
            if nm[host].state() == "up":
                hosts_up += 1
        except Exception:
            pass

        for proto in nm[host].all_protocols():
            if proto not in ("tcp", "udp", "sctp", "ip"):
                continue

            for port in sorted(nm[host][proto].keys()):
                pdata = nm[host][proto][port]
                state = str(pdata.get("state", ""))
                service = str(pdata.get("name", ""))
                product = str(pdata.get("product", ""))
                version = str(pdata.get("version", ""))
                extrainfo = str(pdata.get("extrainfo", ""))
                version_text = " ".join([p for p in [product, version, extrainfo] if p]).strip()

                if state.lower() == "open":
                    open_ports += 1

                results.append(
                    {
                        "host": host,
                        "port": port,
                        "protocol": proto,
                        "state": state,
                        "service": service,
                        "version": version_text,
                    }
                )

    return results, hosts_up, open_ports


class ScanWorker:
    def __init__(
        self,
        *,
        target: str,
        base_args: str,
        ports_arg: str,
        os_detect: bool,
        svc_version: bool,
        timeout_s: int,
        stop_event,
        output_callback: OutputCallback,
    ) -> None:
        self.target = target.strip()
        self.base_args = base_args
        self.ports_arg = ports_arg
        self.os_detect = os_detect
        self.svc_version = svc_version
        # Allow large timeouts; if 0/negative is passed treat it as "no watchdog"
        self.timeout_s = int(timeout_s)
        self.stop_event = stop_event
        self.output = output_callback

        self._proc: subprocess.Popen | None = None

    def stop(self) -> None:
        proc = self._proc
        if not proc:
            return
        try:
            proc.terminate()
        except Exception:
            return
        try:
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def run(self) -> Tuple[List[Dict], int, int, str]:
        if not self.target:
            raise ValueError("Target is required.")

        args, ports = _normalize_args(self.base_args, self.ports_arg, self.os_detect, self.svc_version)

        # user feedback for slow scans
        if any(token in args for token in ("-p-", "-sU", "-sS -T2")):
            self.output("Warning: This scan profile may take several minutes to complete.")

        # Run nmap CLI in this process so we can stream output + stop reliably on Windows.
        # Then parse the produced XML with python-nmap for a structured table.
        xml_file = tempfile.NamedTemporaryFile(delete=False, suffix=".xml")
        xml_path = xml_file.name
        xml_file.close()

        cmd: list[str] = ["nmap"] + args.split()
        if ports:
            cmd += ["-p", ports]
        cmd += [
            "--stats-every",
            "1s",
            "-v",
            "-oX",
            xml_path,
            self.target,
        ]

        start_time = time.time()
        try:
            self.output("Running: " + " ".join(cmd))
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                universal_newlines=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )

            assert self._proc.stdout is not None
            while True:
                if self.stop_event.is_set():
                    self.stop()
                    raise ScanStopped("Scan stopped by user.")

                if self.timeout_s > 0 and (time.time() - start_time) > self.timeout_s:
                    self.stop()
                    raise ScanTimedOut(f"Scan timed out ({self.timeout_s}s).")

                line = self._proc.stdout.readline()
                if line:
                    clean = line.rstrip("\r\n")
                    if clean:
                        self.output(clean)
                    continue

                code = self._proc.poll()
                if code is not None:
                    if code != 0:
                        raise RuntimeError(f"nmap exited with code {code}")
                    break

                time.sleep(0.05)

            try:
                xml_text = open(xml_path, "r", encoding="utf-8", errors="replace").read()
            except Exception as exc:
                raise RuntimeError(f"Failed to read XML output: {exc}") from exc

            nm = nmap.PortScanner()
            nm.analyse_nmap_xml_scan(xml_text)
        finally:
            self._proc = None
            try:
                os.unlink(xml_path)
            except Exception:
                pass

        results, hosts_up, open_ports = _scan_to_rows(nm)
        summary = f"Done - {hosts_up} host(s) up, {open_ports} open port(s)"
        return results, hosts_up, open_ports, summary
