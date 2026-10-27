"""Exercise rendered download-client scripts against the remote-path API."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import subprocess
import threading
import unittest

import yaml

from chart_artifacts import ROOT, chart_input


HOST = "qbittorrent.qbittorrent.svc.cluster.local"


def setup_script(app):
    result = subprocess.run(
        [os.environ.get("HELM", "helm"), "template", app,
         str(chart_input(ROOT / "media-manager" / app))],
        check=True, capture_output=True, text=True,
    )
    return next(resource["data"]["setup-downloadclients.sh"]
                for resource in yaml.safe_load_all(result.stdout)
                if resource and resource["kind"] == "ConfigMap"
                and "setup-downloadclients.sh" in resource.get("data", {}))


class DownloadPathTests(unittest.TestCase):
    def exercise(self, app, mappings=None, get_status=200, post_status=201, malformed=False):
        state = {"mappings": list(mappings or []), "posts": [], "requests": []}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                state["requests"].append(("GET", self.path))
                self.send_response(get_status)
                self.end_headers()
                self.wfile.write(b"not-json" if malformed else json.dumps(state["mappings"]).encode())

            def do_POST(self):
                state["requests"].append(("POST", self.path))
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                state["posts"].append(body)
                self.send_response(post_status)
                self.end_headers()
                if post_status < 400:
                    state["mappings"].append({"id": 1, **body})
                self.wfile.write(b"{}")

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        script = setup_script(app)
        # Source the actual rendered functions without entering the daemon loop.
        functions = script.split('echo "[setup] Starting download client manager for ')[0]
        command = functions + f'''
API_PORT={server.server_port}
ensure_qbittorrent_path_mapping 'secret-api-key' '{HOST}'
first=$?
ensure_qbittorrent_path_mapping 'secret-api-key' '{HOST}'
second=$?
[ "$first" -eq 0 ] && [ "$second" -eq 0 ]
'''
        try:
            result = subprocess.run(["bash", "-c", command], capture_output=True, text=True, timeout=10)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        self.assertNotIn("secret-api-key", result.stdout + result.stderr)
        self.assertTrue(all(path == f"/{app}/api/v3/remotepathmapping"
                            for _, path in state["requests"]))
        return result, state

    def test_creates_mapping_once_across_reconciliation_passes(self):
        for app in ("sonarr", "radarr"):
            with self.subTest(app=app):
                result, state = self.exercise(app)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(state["posts"], [{"host": HOST, "remotePath": "/downloads/",
                                                   "localPath": "/downloads/qbittorrent/"}])

    def test_preserves_existing_user_mapping_with_or_without_trailing_slash(self):
        for app in ("sonarr", "radarr"):
            for remote in ("/downloads", "/downloads/"):
                with self.subTest(app=app, remote=remote):
                    existing = [{"id": 4, "host": HOST.upper(), "remotePath": remote,
                                 "localPath": "/custom/user/path/"}]
                    result, state = self.exercise(app, existing)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(state["posts"], [])
                    self.assertEqual(state["mappings"], existing)

    def test_mapping_for_another_host_does_not_hide_missing_mapping(self):
        for app in ("sonarr", "radarr"):
            with self.subTest(app=app):
                other = {"id": 4, "host": "other-client", "remotePath": "/downloads/",
                         "localPath": "/custom/user/path/"}
                result, state = self.exercise(app, [other])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(state["mappings"][0], other)
                self.assertEqual(len(state["posts"]), 1)

    def test_failed_or_malformed_reads_never_create_mapping(self):
        for app in ("sonarr", "radarr"):
            for options in ({"get_status": 503}, {"malformed": True}):
                with self.subTest(app=app, options=options):
                    result, state = self.exercise(app, **options)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(state["posts"], [])

    def test_failed_write_returns_failure_for_next_pass_to_retry(self):
        for app in ("sonarr", "radarr"):
            with self.subTest(app=app):
                result, state = self.exercise(app, post_status=503)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(len(state["posts"]), 2)
                self.assertEqual(state["mappings"], [])


ROOT_FOLDERS = {"sonarr": "/media/shows", "radarr": "/media/movies"}


class RootFolderTests(unittest.TestCase):
    def exercise(self, app, existing=None, get_status=200, post_status=201, malformed=False):
        state = {"folders": list(existing or []), "posts": [], "requests": []}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                state["requests"].append(("GET", self.path))
                self.send_response(get_status)
                self.end_headers()
                self.wfile.write(b"not-json" if malformed else json.dumps(state["folders"]).encode())

            def do_POST(self):
                state["requests"].append(("POST", self.path))
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                state["posts"].append(body)
                self.send_response(post_status)
                self.end_headers()
                if post_status < 400:
                    state["folders"].append({"id": 1, **body})
                self.wfile.write(b"{}")

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        script = setup_script(app)
        functions = script.split('echo "[setup] Starting download client manager for ')[0]
        command = functions + f'''
API_PORT={server.server_port}
ensure_root_folder 'secret-api-key'
first=$?
ensure_root_folder 'secret-api-key'
second=$?
[ "$first" -eq 0 ] && [ "$second" -eq 0 ]
'''
        try:
            result = subprocess.run(["bash", "-c", command], capture_output=True, text=True, timeout=10)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        self.assertNotIn("secret-api-key", result.stdout + result.stderr)
        self.assertTrue(all(path == f"/{app}/api/v3/rootfolder"
                            for _, path in state["requests"]))
        return result, state

    def test_registers_the_library_root_folder_once(self):
        for app, path in ROOT_FOLDERS.items():
            with self.subTest(app=app):
                result, state = self.exercise(app)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(state["posts"], [{"path": path}])

    def test_existing_root_folder_is_never_replaced(self):
        for app, path in ROOT_FOLDERS.items():
            for stored in (path, path + "/"):
                with self.subTest(app=app, stored=stored):
                    result, state = self.exercise(app, [{"id": 7, "path": stored}])
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(state["posts"], [])

    def test_unrelated_root_folder_does_not_hide_a_missing_one(self):
        for app, path in ROOT_FOLDERS.items():
            with self.subTest(app=app):
                result, state = self.exercise(app, [{"id": 7, "path": "/elsewhere"}])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(state["posts"], [{"path": path}])

    def test_failed_or_malformed_read_never_registers(self):
        for app in ROOT_FOLDERS:
            for options in ({"get_status": 503}, {"malformed": True}):
                with self.subTest(app=app, options=options):
                    result, state = self.exercise(app, **options)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(state["posts"], [])

    def test_failed_write_returns_failure_for_next_pass_to_retry(self):
        for app in ROOT_FOLDERS:
            with self.subTest(app=app):
                result, state = self.exercise(app, post_status=503)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(len(state["posts"]), 2)
                self.assertEqual(state["folders"], [])


if __name__ == "__main__":
    unittest.main()
