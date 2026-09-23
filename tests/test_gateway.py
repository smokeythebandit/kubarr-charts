"""Execute the rendered catch-all gateway access block with mocked ngx APIs."""

import json
import os
from pathlib import Path
import re
import subprocess
import unittest

import yaml

from chart_artifacts import chart_input


ROOT = Path(__file__).resolve().parents[1]


class GatewayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = subprocess.run(
            [os.environ.get("HELM", "helm"), "template", "gateway",
             str(chart_input(ROOT / "system/openresty"))],
            check=True, capture_output=True, text=True,
        )
        config, = [
            resource["data"]["nginx.conf"]
            for resource in yaml.safe_load_all(result.stdout)
            if resource and resource["kind"] == "ConfigMap"
            and "nginx.conf" in resource.get("data", {})
        ]
        # Match the catch-all location, then its indentation-delimited Lua block.
        location, = re.findall(r"^ *location ~ .*\(\?<app_name>.*$", config, re.M)
        match = re.search(
            r"^( *)access_by_lua_block \{\n(.*?)^\1\}",
            config.split(location, 1)[1], re.M | re.S,
        )
        if match is None:
            raise AssertionError("Catch-all access block not found")
        cls.config = config
        cls.access_lua = match.group(2)

    def check_gateway(self, setup="", outcome="proxy", captures=1, checks=""):
        script = r'''
local route = {status = 404, header = {}}
local auth = {status = 404, header = {}}
local catalog = {status = 404, header = {}}
local outcome = "proxy"
local captures = 0
ngx = {
  HTTP_OK = 200, HTTP_NO_CONTENT = 204, HTTP_MOVED_TEMPORARILY = 302,
  HTTP_UNAUTHORIZED = 401, HTTP_FORBIDDEN = 403, HTTP_NOT_FOUND = 404,
  HTTP_INTERNAL_SERVER_ERROR = 500, HTTP_BAD_GATEWAY = 502,
  HTTP_SERVICE_UNAVAILABLE = 503, HTTP_GATEWAY_TIMEOUT = 504, ERR = "error",
  var = {
    host = "kubarr.test", uri = "/nonexistent-route",
    app_name = "nonexistent-route", app_path = "",
    app_upstream = "", app_base_path = "", target_path = ""
  },
  location = {},
  log = function() end,
  exit = function(status) outcome = "exit:" .. status end,
  exec = function(location) outcome = "exec:" .. location end,
  escape_uri = function(value) return value end,
  redirect = function(uri, status)
    outcome = "redirect:" .. status .. ":" .. uri
  end
}
ngx.location.capture = function(uri, options)
  captures = captures + 1
  if captures == 1 then
    assert(uri == "/_kubarr_route_lookup", uri)
    assert(options.args.host == ngx.var.host)
    assert(options.args.path == ngx.var.uri)
    return route
  end
  if captures == 2 then
    assert(uri == "/_kubarr_app_auth/" .. ngx.var.app_name, uri)
    assert(options == nil)
    return auth
  end
  assert(captures == 3, "Unexpected extra subrequest")
  assert(uri == "/_kubarr_catalog_lookup/" .. ngx.var.app_name, uri)
  assert(options == nil)
  return catalog
end
'''
        script += setup + "\nlocal function access()\n" + self.access_lua
        script += "\nend\naccess()\n"
        script += f"assert(outcome == {json.dumps(outcome)}, outcome)\n"
        script += f"assert(captures == {captures}, captures)\n" + checks
        result = subprocess.run(
            ["luajit", "-"], input=script,
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_unknown_frontend_path_serves_spa(self):
        self.check_gateway(outcome="exec:@frontend_index", captures=3)

    def test_known_unavailable_app_redirects_to_status_ui(self):
        self.check_gateway(
            setup="catalog.status = 200",
            outcome=("redirect:302:/app-error?app=nonexistent-route"
                     "&reason=not_found"),
            captures=3,
        )

    def test_catalog_lookup_proxy_is_internal_and_authenticated(self):
        self.assertRegex(
            self.config,
            r"location ~ \^/_kubarr_catalog_lookup/"
            r"\(\?<catalog_app_name>\[a-z0-9-\]\+\)\$ \{\n"
            r"\s+internal;\n"
            r"\s+proxy_pass http://kubarr_api/api/apps/catalog/"
            r"\$catalog_app_name;\n"
            r"\s+proxy_pass_request_body off;\n"
            r"\s+proxy_set_header Content-Length \"\";\n"
            r"\s+proxy_set_header Cookie \$http_cookie;",
        )

    def test_denials_are_preserved(self):
        for source in ("route", "auth"):
            for status in (401, 403):
                with self.subTest(source=source, status=status):
                    self.check_gateway(
                        f"{source}.status = {status}",
                        outcome=f"exit:{status}",
                        captures=1 if source == "route" else 2,
                    )

    def test_unexpected_lookup_errors_do_not_serve_frontend(self):
        for source, statuses in (("route", (301, 500, 502, 503, 504)),
                                 ("auth", (301,))):
            for status in statuses:
                with self.subTest(source=source, status=status):
                    self.check_gateway(
                        f"{source}.status = {status}", outcome="exit:500",
                        captures=1 if source == "route" else 2,
                    )

    def test_app_connection_errors_redirect_to_status_ui(self):
        for status in (500, 502, 503, 504):
            with self.subTest(status=status):
                self.check_gateway(
                    f"auth.status = {status}",
                    outcome=("redirect:302:/app-error?app=nonexistent-route"
                             "&reason=connection_failed"),
                    captures=2,
                )

    def test_catalog_lookup_denials_are_preserved(self):
        for status in (401, 403):
            with self.subTest(status=status):
                self.check_gateway(
                    f"catalog.status = {status}",
                    outcome=f"exit:{status}", captures=3,
                )

    def test_catalog_lookup_errors_do_not_serve_frontend(self):
        for status in (204, 301, 500, 502, 503, 504):
            with self.subTest(status=status):
                self.check_gateway(
                    f"catalog.status = {status}",
                    outcome="exit:500", captures=3,
                )

    def test_success_requires_routing_headers(self):
        for source, header in (
            ("route", ""),
            ("route", '["x-kubarr-app-name"] = "sonarr"'),
            ("route", '["x-kubarr-upstream"] = "http://sonarr:8989"'),
            ("route", '["x-kubarr-app-name"] = "sonarr", '
                      '["x-kubarr-upstream"] = ""'),
            ("auth", ""),
            ("auth", '["x-kubarr-upstream"] = ""'),
        ):
            for status in (200, 204):
                with self.subTest(source=source, header=header, status=status):
                    self.check_gateway(
                        f"{source}.status = {status}\n{source}.header = {{{header}}}",
                        outcome="exit:500", captures=1 if source == "route" else 2,
                    )

    def test_route_lookup_success(self):
        for status in (200, 204):
            for target in (None, "/calendar"):
                with self.subTest(status=status, target=target):
                    self.check_gateway(
                        f'''
route.status = {status}
route.header = {{
  ["x-kubarr-app-name"] = "sonarr",
  ["x-kubarr-upstream"] = "http://sonarr:8989",
  ["x-kubarr-base-path"] = "/sonarr",
  ["x-kubarr-target-path"] = {json.dumps(target) if target else "nil"}
}}
''',
                        checks=f'''
assert(ngx.var.app_name == "sonarr")
assert(ngx.var.app_upstream == "http://sonarr:8989")
assert(ngx.var.app_base_path == "/sonarr")
assert(ngx.var.target_path == {json.dumps(target or '/nonexistent-route')})
''',
                    )

    def test_fallback_app_auth_success(self):
        for status in (200, 204):
            for base_path in ("", "/sonarr"):
                for app_path in ("", "/", "/calendar"):
                    with self.subTest(status=status, base=base_path, path=app_path):
                        self.check_gateway(
                            f'''
ngx.var.app_name = "sonarr"
ngx.var.app_path = {json.dumps(app_path)}
ngx.var.uri = "/sonarr" .. ngx.var.app_path
auth.status = {status}
auth.header = {{
  ["x-kubarr-upstream"] = "http://sonarr:8989",
  ["x-kubarr-base-path"] = {json.dumps(base_path)}
}}
''',
                            captures=2,
                            checks=f'''
assert(ngx.var.app_name == "sonarr")
assert(ngx.var.app_upstream == "http://sonarr:8989")
assert(ngx.var.app_base_path == {json.dumps(base_path)})
assert(ngx.var.target_path == {json.dumps('/sonarr' + app_path if base_path else app_path or '/')})
''',
                        )

    def test_fallback_app_landing_redirect(self):
        self.check_gateway(
            '''
ngx.var.app_name = "plex"
ngx.var.uri = "/plex"
auth.status = 200
auth.header = {
  ["x-kubarr-upstream"] = "http://plex:32400",
  ["x-kubarr-landing-path"] = "/web/"
}
''',
            outcome="redirect:302:/plex/web/", captures=2,
        )


if __name__ == "__main__":
    unittest.main()
