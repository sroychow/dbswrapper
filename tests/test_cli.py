from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dbs2go_wrapper.cli import main


DATASET = "/Primary/Processed-v1/MINIAOD"
PARENT_CHAIN = {
    DATASET: ["/Parent/Processed/GEN-SIM"],
    "/Parent/Processed/GEN-SIM": ["/Grandparent/Processed/GEN"],
    "/Grandparent/Processed/GEN": [],
}
CHILD_CHAIN = {
    DATASET: ["/Child/Processed/NANOAOD"],
    "/Child/Processed/NANOAOD": ["/Grandchild/Processed/NANOAOD"],
    "/Grandchild/Processed/NANOAOD": [],
}


class FakeDBSHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        parsed = urlparse(self.path)
        endpoint = parsed.path.rstrip("/").split("/")[-1]
        params = parse_qs(parsed.query)

        payload: object
        if endpoint == "datasets":
            payload = [
                {
                    "dataset": DATASET,
                    "dataset_access_type": "VALID",
                    "data_tier_name": "MINIAOD",
                    "detail_requested": params.get("detail", [""])[0],
                }
            ]
        elif endpoint == "filesummaries":
            payload = [{"num_file": 2, "num_event": 100, "file_size": 2048}]
        elif endpoint == "runs":
            payload = [{"run_num": 123456}]
        elif endpoint == "blocks":
            payload = [{"block_name": f"{DATASET}#block", "file_count": 2}]
        elif endpoint == "outputconfigs":
            payload = [{"release_version": "CMSSW_X_Y_Z", "global_tag": "TEST"}]
        elif endpoint == "datasetparents":
            payload = [
                {"parent_dataset": parent}
                for parent in PARENT_CHAIN.get(params.get("dataset", [""])[0], [])
            ]
        elif endpoint == "datasetchildren":
            payload = [
                {"child_dataset": child}
                for child in CHILD_CHAIN.get(params.get("dataset", [""])[0], [])
            ]
        elif endpoint == "files":
            payload = [{"logical_file_name": "/store/test.root", "event_count": 100}]
        else:
            self.send_response(404)
            self.end_headers()
            return

        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_server() -> tuple[ThreadingHTTPServer, threading.Thread, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeDBSHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, thread, f"http://{host}:{port}"


def test_dump_command_writes_bundle_and_manifest(tmp_path: Path) -> None:
    server, thread, base_url = run_server()
    output = tmp_path / "dump"
    try:
        exit_code = main(
            [
                "dump",
                DATASET,
                "--base-url",
                base_url,
                "--no-cert",
                "--output",
                str(output),
                "--include-files",
                "--include-hierarchy",
                "--workers",
                "2",
            ]
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)

    assert exit_code == 0
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["counts"]["matched"] == 1
    assert manifest["counts"]["successful"] == 1

    target = output / "datasets" / "Primary" / "Processed-v1" / "MINIAOD"
    bundle = json.loads((target / "bundle.json").read_text(encoding="utf-8"))
    assert bundle["dataset"] == DATASET
    assert bundle["sections"]["summary"][0]["num_event"] == 100
    assert bundle["sections"]["files"][0]["logical_file_name"] == "/store/test.root"
    hierarchy = bundle["sections"]["hierarchy"]
    assert hierarchy == {
        "dataset": DATASET,
        "parents": [
            {
                "dataset": "/Parent/Processed/GEN-SIM",
                "parents": [{"dataset": "/Grandparent/Processed/GEN", "parents": []}],
            }
        ],
        "children": [
            {
                "dataset": "/Child/Processed/NANOAOD",
                "children": [{"dataset": "/Grandchild/Processed/NANOAOD", "children": []}],
            }
        ],
    }
    assert json.loads((target / "hierarchy.json").read_text(encoding="utf-8")) == hierarchy
