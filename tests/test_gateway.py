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
        cls.app_location = config.split(location, 1)[1]
        cls.header_lua = re.search(r"^( *)header_filter_by_lua_block \{\n(.*?)^\1\}", cls.app_location, re.M | re.S).group(2)
        cls.body_lua = re.search(r"^( *)body_filter_by_lua_block \{\n(.*?)^\1\}", cls.app_location, re.M | re.S).group(2)
        api_block = config.split("location /api/ {", 1)[1]
        api_match = re.search(r"^( *)access_by_lua_block \{\n(.*?)^\1\}", api_block, re.M | re.S)
        if api_match is None:
            raise AssertionError("API route access block not found")
        cls.api_access_lua = api_match.group(2)
        auth_block = config.split("location /auth/ {", 1)[1]
        cls.auth_access_lua = re.search(r"^( *)access_by_lua_block \{\n(.*?)^\1\}", auth_block, re.M | re.S).group(2)
        frontend_block = config.split("location / {", 1)[1]
        cls.frontend_header_lua = re.search(
            r"^( *)header_filter_by_lua_block \{\n(.*?)^\1\}",
            frontend_block, re.M | re.S).group(2)
        failure_block = config.split("location @app_connection_failed {", 1)[1]
        cls.connection_failure_lua = re.search(r"^( *)content_by_lua_block \{\n(.*?)^\1\}", failure_block, re.M | re.S).group(2)

    def test_api_connection_failures_preserve_errors_instead_of_returning_html(self):
        script = r'''
local exited, redirect, body
local method = "GET"
ngx = {
 HTTP_BAD_GATEWAY=502, HTTP_MOVED_TEMPORARILY=302,
 header={}, var={app_name="jellyfin", upstream_status="502"},
 req={get_method=function() return method end},
 say=function(value) body=value end,
 exit=function(status) exited=status end,
 escape_uri=function(value) return value end,
 redirect=function(uri, status) redirect={uri,status} end
}
'''
        script += "\nlocal function failure()\n" + self.connection_failure_lua + "\nend\n"
        script += r'''
for _, accept in ipairs({"", "application/json, text/plain, */*", "*/*", "text/event-stream"}) do
 for _, status in ipairs({502,503,504}) do
  ngx.header = { ["Content-Length"]="old" }
  ngx.var.http_accept=accept
  ngx.var.upstream_status="502, " .. status
  exited, redirect, body=nil,nil,nil
  failure()
  assert(exited==status and ngx.status==status)
  assert(redirect==nil and body=='{"error":"app_connection_failed"}')
  assert(ngx.header["Content-Type"]=="application/json")
  assert(ngx.header["Cache-Control"]=="no-store")
  assert(ngx.header["Content-Length"]==nil)
 end
end
method="POST"
ngx.var.http_accept="text/html"
ngx.var.upstream_status="502"
exited, redirect=nil,nil
failure()
assert(exited==502 and redirect==nil)
for _, navigation_method in ipairs({"GET","HEAD"}) do
 method=navigation_method
 exited, redirect=nil,nil
 failure()
 assert(exited==nil and redirect[2]==302)
 assert(redirect[1]=="/app-error?app=jellyfin&reason=connection_failed")
end
'''
        result = subprocess.run(["luajit", "-"], input=script, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_api_auth_failures_do_not_redirect_to_status_ui(self):
        for status in (500, 502, 503, 504):
            with self.subTest(status=status):
                self.check_gateway(
                    f'auth.status = {status}\nngx.var.http_accept = "application/json"',
                    outcome=f"exit:{status}", captures=2,
                )
        self.check_gateway(
            'catalog.status = 200\nngx.var.http_accept = "application/json"',
            outcome="exit:404", captures=3,
        )

    def test_host_app_auth_html_does_not_intercept_kubarr_login_or_host_transfer(self):
        script = r'''
local captured = 0
local method = "POST"
local route = {status=204, header={["x-kubarr-route-mode"]="exact_host", ["x-kubarr-upstream"]="http://immich:2283"}}
local exited
ngx = {
 HTTP_OK=200, HTTP_NO_CONTENT=204, HTTP_UNAUTHORIZED=401, HTTP_FORBIDDEN=403,
 HTTP_NOT_FOUND=404, HTTP_INTERNAL_SERVER_ERROR=500,
 var={host="photos.test", uri="/auth/login", request_uri="/auth/login", http_accept="text/html", auth_upstream="http://kubarr_api"},
 req={get_method=function() return method end},
 exit=function(code) exited=code end,
 location={capture=function() captured=captured+1; return route end}
}
'''
        script += "\nlocal function access()\n" + self.auth_access_lua + "\nend\n"
        script += r'''
access()
assert(captured == 0 and ngx.var.auth_upstream == "http://kubarr_api")
method = "GET"
ngx.var.uri = "/auth/host-transfer/complete"
access()
assert(captured == 0)
ngx.var.uri = "/auth/login"
ngx.var.http_accept = "application/json"
access()
assert(captured == 0)
ngx.var.http_accept = "text/html"
access()
assert(captured == 1 and ngx.var.auth_upstream == "http://immich:2283")
route = {status=403, header={}}
access()
assert(exited == 403)
exited = nil
route = {status=404, header={}}
ngx.var.auth_upstream = "http://kubarr_api"
access()
assert(exited == nil and ngx.var.auth_upstream == "http://kubarr_api")
'''
        result = subprocess.run(["luajit", "-"], input=script, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_api_host_routing_preserves_kubarr_login_and_denies_unauthorized_apps(self):
        script = r'''
local route = {status = 404, header = {}}
local captured = 0
local exit_status = nil
ngx = {
  HTTP_OK = 200, HTTP_NO_CONTENT = 204, HTTP_NOT_FOUND = 404,
  HTTP_UNAUTHORIZED = 401, HTTP_FORBIDDEN = 403, HTTP_INTERNAL_SERVER_ERROR = 500,
  var = {host = "photos.localhost", uri = "/api/server/config", api_upstream = "http://kubarr_api"},
  exit = function(status) exit_status = status end,
  location = {capture = function(uri, options)
    captured = captured + 1
    assert(uri == "/_kubarr_route_lookup")
    assert(options.args.host == ngx.var.host and options.args.path == ngx.var.uri)
    return route
  end}
}
'''
        script += "\nlocal function access()\n" + self.api_access_lua + "\nend\n"
        script += r'''
ngx.var.uri = "/api/system/health"
access()
assert(captured == 0 and ngx.var.api_upstream == "http://kubarr_api")
route.status = 401
for _, uri in ipairs({"/api/oauth/available", "/api/oauth/google/login", "/api/oauth/microsoft/callback"}) do
  ngx.var.uri = uri
  access()
  assert(captured == 0 and ngx.var.api_upstream == "http://kubarr_api")
end
ngx.var.uri = "/api/oauth/providers"
access()
assert(captured == 1 and exit_status == 401)
captured = 0
exit_status = nil
route.status = 404
ngx.var.host = "localhost"
ngx.var.uri = "/api/users/me"
access()
assert(captured == 1 and ngx.var.api_upstream == "http://kubarr_api")
ngx.var.host = "photos.localhost"
ngx.var.uri = "/api/server/config"
access()
assert(captured == 2 and ngx.var.api_upstream == "http://kubarr_api")
route = {status = 204, header = {["x-kubarr-route-mode"] = "exact_host", ["x-kubarr-upstream"] = "http://immich:2283"}}
ngx.var.uri = "/api/users/me"
access()
assert(captured == 3 and ngx.var.api_upstream == "http://immich:2283")
route = {status = 403, header = {}}
access()
assert(exit_status == 403)
'''
        result = subprocess.run(["luajit", "-"], input=script, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_app_404_does_not_fall_back_to_kubarr_login(self):
        self.assertIn('error_page 404 = @app_or_frontend_index;', self.config)
        self.assertIn('if ngx.var.app_name ~= "" then\n', self.config)
        self.assertIn('return ngx.exec("@frontend_index")', self.config)

    def test_html_rewrite_drops_stale_response_length_and_validators(self):
        script = r'''
ngx = {
  header = {["Content-Type"]="text/html; charset=utf-8", ["Content-Length"]="23", ["ETag"]="old", ["Content-MD5"]="old"},
  var = {app_name="deluge", app_base_path=""},
  arg = {'<script src="/main.js">', true}
}
'''
        script += "\nlocal function headers()\n" + self.header_lua + "\nend\n"
        script += "\nlocal function body()\n" + self.body_lua + "\nend\n"
        script += r'''
headers()
body()
assert(ngx.arg[1] == '<script src="/deluge/main.js">')
assert(ngx.header["Content-Length"] == nil)
assert(ngx.header["ETag"] == nil and ngx.header["Content-MD5"] == nil)
ngx.header = {["Content-Type"]="application/json", ["Content-Length"]="2", ["ETag"]="json"}
ngx.arg[1] = '{}'
headers()
body()
assert(ngx.header["Content-Length"] == "2" and ngx.arg[1] == '{}')
ngx.header = {["Content-Type"]="text/html", ["Content-Length"]="23", ["ETag"]="native"}
ngx.var.app_base_path = "/sonarr"
ngx.arg[1] = '<script src="/sonarr/main.js">'
headers()
body()
assert(ngx.header["Content-Length"] == "23" and ngx.header["ETag"] == "native")
assert(ngx.arg[1] == '<script src="/sonarr/main.js">')
ngx.var.app_name = "frigate"
ngx.var.app_base_path = ""
ngx.arg[1] = '<script src="/frigate/assets/main.js">'
headers()
body()
assert(ngx.arg[1] == '<script src="/frigate/assets/main.js">')
assert(ngx.header["Content-Length"] == "23")
'''
        result = subprocess.run(["luajit", "-"], input=script, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('proxy_set_header Accept-Encoding "";', self.app_location.split('header_filter_by_lua_block', 1)[0])
        self.assertIn('proxy_set_header X-Ingress-Path $app_ingress_path;', self.app_location)

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
    host = "kubarr.test", uri = "/nonexistent-route", http_accept = "text/html",
    app_name = "nonexistent-route", app_path = "",
    app_upstream = "", app_base_path = "", app_ingress_path = "", target_path = ""
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

    # Each proxied request and internal lookup must reuse a pooled connection.
    # Without this the gateway opens a fresh TCP connection to the API for every
    # route lookup and every proxied asset, which a page fetching many assets
    # pays for over and over.

    def upstream(self, name):
        block = re.search(rf"upstream {name} \{{\n(.*?)\n *\}}", self.config, re.S)
        self.assertIsNotNone(block, f"upstream {name} not found")
        return block.group(1)

    def test_both_upstreams_pool_connections(self):
        for name in ("kubarr_api", "kubarr_frontend"):
            with self.subTest(upstream=name):
                self.assertRegex(self.upstream(name), r"keepalive \d+;")

    def test_internal_lookups_speak_http_11_without_closing_the_connection(self):
        for marker in ("location = /_kubarr_route_lookup {",
                       "location ~ ^/_kubarr_app_auth/",
                       "location ~ ^/_kubarr_catalog_lookup/"):
            with self.subTest(location=marker):
                tail = self.config.split(marker, 1)[1]
                block = tail.split("\n        }", 1)[0]
                self.assertIn("proxy_http_version 1.1;", block)
                self.assertRegex(block, r'proxy_set_header Connection "";')

    def test_ordinary_requests_do_not_ask_the_upstream_to_close(self):
        # Websockets still need "upgrade"; everything else must omit Connection
        # so the pooled connection survives the response.
        upgrade_map = re.search(r"map \$http_upgrade \$connection_upgrade \{\n(.*?)\n *\}",
                                self.config, re.S)
        self.assertIsNotNone(upgrade_map)
        body = upgrade_map.group(1)
        self.assertRegex(body, r"default\s+upgrade;")
        self.assertNotRegex(body, r"''\s+close;")
        self.assertRegex(body, r"''\s+\"\";")

    # The gateway resolves a route and an app authorization on every proxied
    # request. A shared-memory cache keyed by session stops one page's many
    # asset requests from each paying for the same backend round trip.
    def init_lua(self):
        # Delimited by its own indentation, as the other Lua blocks are.
        match = re.search(r"^( *)init_by_lua_block \{\n(.*?)^\1\}",
                          self.config, re.M | re.S)
        self.assertIsNotNone(match, "init_by_lua_block not found")
        return match.group(2)

    def cache_script(self, *body):
        # Stub the digest modules and a shared dictionary, then load the real
        # rendered helper so these assertions exercise shipped Lua.
        harness = [
            "local stored, ttls, captured = {}, {}, 0",
            "local digested = {}",
            "package.loaded['resty.sha256'] = {",
            "  new = function(self)",
            "    local buffer = ''",
            "    return {",
            "      update = function(_, value)",
            "        buffer = buffer .. value; digested[#digested+1] = value end,",
            "      -- Input-sensitive but not reversible, like a real digest.",
            "      final = function()",
            "        local sum = 0",
            "        for index = 1, #buffer do sum = sum + buffer:byte(index) * index end",
            "        return #buffer .. ':' .. sum end } end }",
            "package.loaded['resty.string'] = {",
            "  to_hex = function(value) return 'hex-' .. value end }",
            "local result = {status = 204, header = {",
            "  ['x-kubarr-app-name'] = 'sonarr',",
            "  ['x-kubarr-upstream'] = 'http://sonarr:8989',",
            "  ['x-kubarr-target-path'] = '/api/v3/system' }}",
            "ngx = {",
            "  shared = {kubarr_lookups = {",
            "    get = function(_, key) return stored[key] end,",
            "    set = function(_, key, value, ttl)",
            "      stored[key] = value; ttls[key] = ttl end }},",
            "  location = {capture = function() captured = captured + 1",
            "    return result end},",
            "  var = {http_cookie = 'kubarr_active=0; kubarr_session_0=tok; other=x'},",
            "  log = function() end, ERR = 'error' }",
        ]
        return "\n".join(harness) + "\n" + self.init_lua() + "\n" + "\n".join(body)

    def run_lua(self, script):
        outcome = subprocess.run(["luajit", "-"], input=script,
                                 capture_output=True, text=True)
        self.assertEqual(outcome.returncode, 0, outcome.stdout + outcome.stderr)

    def test_text_responses_are_compressed_for_the_client(self):
        # Upstreams are asked for identity encoding so the Lua body filter can
        # rewrite HTML; without gzip here the client then receives a 1MB
        # bundle raw, which dwarfs every other per-request cost.
        self.assertRegex(self.config, r"\n\s+gzip on;")
        self.assertRegex(self.config, r"\n\s+gzip_vary on;")
        # Responses arrive via proxy_pass, so compression must apply to them.
        self.assertRegex(self.config, r"\n\s+gzip_proxied any;")
        types, = re.findall(r"gzip_types\s+([^;]+);", self.config)
        for mime in ("text/css", "application/javascript", "application/json",
                     "image/svg+xml"):
            self.assertIn(mime, types)
        # Already-compressed payloads must not be run through gzip again.
        for mime in ("image/png", "image/jpeg", "font/woff2", "video/"):
            self.assertNotIn(mime, types)
        level, = re.findall(r"gzip_comp_level (\d+);", self.config)
        self.assertIn(int(level), range(1, 7), "high levels cost CPU per request")
        minimum, = re.findall(r"gzip_min_length (\d+);", self.config)
        self.assertGreaterEqual(int(minimum), 256, "tiny bodies are not worth it")
    def frontend_headers(self, *body):
        harness = [
            "ngx = {header = {}, var = {}}",
        ]
        script = "\n".join(harness) + "\n"
        script += "local function filter()\n" + self.frontend_header_lua + "\nend\n"
        script += "\n".join(body)
        self.run_lua(script)

    def test_content_hashed_assets_are_cached_forever(self):
        # The filename carries a content hash, so the bytes can never change.
        # Without this the browser revalidates every asset on every visit.
        self.frontend_headers(
            'ngx.var.app_name = ""',
            'ngx.var.uri = "/assets/index-DD3Bqp8e.js"',
            'filter()',
            'local value = ngx.header["Cache-Control"]',
            'assert(value, "hashed assets need a Cache-Control header")',
            'assert(value:find("immutable", 1, true), value)',
            'assert(value:find("max-age=31536000", 1, true), value)',
        )

    def test_the_spa_document_must_always_revalidate(self):
        # A stale index would reference asset hashes a deploy has removed.
        for uri in ("/", "/login", "/settings"):
            with self.subTest(uri=uri):
                self.frontend_headers(
                    'ngx.var.app_name = ""',
                    f'ngx.var.uri = "{uri}"',
                    'filter()',
                    'local value = ngx.header["Cache-Control"]',
                    'assert(value == "no-cache", tostring(value))',
                )

    def test_host_routed_app_responses_keep_their_own_caching(self):
        # Immich and friends serve their own /assets/ through this location.
        # Overriding their headers would cache another product's files wrongly.
        self.frontend_headers(
            'ngx.var.app_name = "immich"',
            'ngx.var.uri = "/assets/immich-bundle.js"',
            'ngx.header["Location"] = "http://photos.test/web"',
            'filter()',
            'assert(ngx.header["Cache-Control"] == nil,',
            '  "an app response must keep its own caching")',
            'assert(ngx.header["Location"] == "/web", ngx.header["Location"])',
        )

    def test_status_page_assets_and_spa_fallback_declare_caching(self):
        for marker, expected in (("location ^~ /_kubarr_assets/ {", "immutable"),
                                 ("location @frontend_index {", "no-cache")):
            with self.subTest(location=marker):
                tail = self.config.split(marker, 1)[1]
                block = re.split(r"\n {4}\}", tail, maxsplit=1)[0]
                self.assertRegex(block, r"add_header Cache-Control [^;]*"
                                 + expected)
    def test_lookup_cache_dictionary_is_declared(self):
        self.assertRegex(self.config, r"lua_shared_dict kubarr_lookups \S+;")

    def test_a_cached_grant_is_served_without_a_second_subrequest(self):
        self.run_lua(self.cache_script(
            'local key = kubarr_cache.key("route", "host|/api/v3/system")',
            'local first = kubarr_cache.capture("/_kubarr_route_lookup", {}, key)',
            'assert(first.status == 204 and captured == 1, captured)',
            'assert(stored[key], "a grant must be stored")',
            'local second = kubarr_cache.capture("/_kubarr_route_lookup", {}, key)',
            'assert(captured == 1, "a cached grant must not reach the backend")',
            'assert(second.status == 204, second.status)',
            'assert(second.header["x-kubarr-upstream"] == "http://sonarr:8989")',
            'assert(second.header["x-kubarr-target-path"] == "/api/v3/system")',
            'assert(second.header["x-kubarr-base-path"] == nil, "absent headers stay absent")',
        ))

    def test_the_key_hashes_the_session_and_never_holds_the_raw_token(self):
        self.run_lua(self.cache_script(
            'local key = kubarr_cache.key("auth", "sonarr")',
            'assert(key:find("hex-", 1, true), key)',
            'assert(not key:find("tok", 1, true), "the session token must not appear in the key")',
            'assert(#digested == 1, #digested)',
            'assert(digested[1]:find("kubarr_active=0", 1, true), digested[1])',
            'assert(digested[1]:find("kubarr_session_0=tok", 1, true), digested[1])',
            'assert(not digested[1]:find("other=x", 1, true), "only kubarr cookies key the cache")',
        ))

    def test_distinct_sessions_and_targets_never_share_an_entry(self):
        self.run_lua(self.cache_script(
            'local a = kubarr_cache.key("auth", "sonarr")',
            'ngx.var.http_cookie = "kubarr_active=0; kubarr_session_0=other-token"',
            'local b = kubarr_cache.key("auth", "sonarr")',
            'assert(a ~= b, "a different session must produce a different key")',
            'local c = kubarr_cache.key("auth", "radarr")',
            'assert(b ~= c, "a different app must produce a different key")',
        ))

    def test_requests_without_a_session_are_never_cached(self):
        self.run_lua(self.cache_script(
            'ngx.var.http_cookie = "other=x"',
            'assert(kubarr_cache.key("route", "host|/") == nil)',
            'kubarr_cache.capture("/_kubarr_route_lookup", {}, nil)',
            'kubarr_cache.capture("/_kubarr_route_lookup", {}, nil)',
            'assert(captured == 2, "an anonymous lookup must not share an entry")',
            'assert(next(stored) == nil)',
        ))

    def test_denials_and_errors_are_never_cached(self):
        for status in (401, 403, 500, 502, 503, 504):
            with self.subTest(status=status):
                self.run_lua(self.cache_script(
                    f'result.status = {status}',
                    'kubarr_cache.capture("/_kubarr_app_auth/sonarr", nil, "k")',
                    f'assert(next(stored) == nil, "{status} must never be cached")',
                    'kubarr_cache.capture("/_kubarr_app_auth/sonarr", nil, "k")',
                    'assert(captured == 2, "a denial must be re-checked")',
                ))

    def test_entries_expire_with_the_configured_window(self):
        self.run_lua(self.cache_script(
            'kubarr_cache.capture("/_kubarr_route_lookup", {}, "k")',
            'assert(kubarr_cache.ttl > 0, "a zero window would disable the cache")',
            'assert(ttls["k"] == kubarr_cache.ttl, tostring(ttls["k"]))',
        ))

    def test_without_shared_memory_every_lookup_reaches_the_backend(self):
        self.run_lua(self.cache_script(
            'ngx.shared = nil',
            'local r = kubarr_cache.capture("/_kubarr_route_lookup", {}, "k")',
            'assert(r.status == 204 and captured == 1)',
            'kubarr_cache.capture("/_kubarr_route_lookup", {}, "k")',
            'assert(captured == 2, "no shared memory means no caching")',
        ))

    def test_lookups_use_the_cache_helper_keyed_by_session(self):
        for marker in ('location ~ ^/(?!(api|auth', 'location ~ ^/(?<absolute_app_path>'):
            with self.subTest(location=marker):
                tail = self.config.split(marker, 1)[1]
                block = re.split(r"\n {4}\}", tail, maxsplit=1)[0]
                self.assertIn("(kubarr_cache or ngx.location).capture", block)
                self.assertIn("kubarr_cache.key(", block)

    def test_catalog_lookup_proxy_is_internal_and_authenticated(self):
        # Assert the properties rather than the directive order, so adding
        # connection-reuse settings to the location cannot fail this.
        tail = self.config.split(
            "location ~ ^/_kubarr_catalog_lookup/"
            "(?<catalog_app_name>[a-z0-9-]+)$ {", 1)[1]
        block = tail.split("\n        }", 1)[0]
        for directive in (
            "internal;",
            "proxy_pass http://kubarr_api/api/apps/catalog/$catalog_app_name;",
            "proxy_pass_request_body off;",
            'proxy_set_header Content-Length "";',
            "proxy_set_header Cookie $http_cookie;",
        ):
            self.assertIn(directive, block)

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
assert(ngx.var.app_ingress_path == "")
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
assert(ngx.var.app_ingress_path == "/sonarr")
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
