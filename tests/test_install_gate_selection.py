"""Which charts the publish-time install gate selects.

Publish once gated "charts changed since the previous commit". A CI-only
commit then selected nothing and pushed five charts an earlier, failed publish
had left in the tree -- none of them installed. The gate now selects what the
push step will push: every application chart whose version is not in the
registry yet. These tests pin that rule with a fake registry.
"""

import importlib.util
import shutil
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent

try:
    import yaml  # noqa: F401  (the harness needs it)
except ImportError:  # pragma: no cover
    yaml = None


def load_gate():
    spec = importlib.util.spec_from_file_location("install_gate", HERE / "e2e" / "install_gate.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("install_gate", module)
    spec.loader.exec_module(module)
    return module


@unittest.skipIf(yaml is None, "PyYAML is required")
class UnpublishedSelection(unittest.TestCase):
    def setUp(self):
        self.gate = load_gate()
        self.tree = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tree, ignore_errors=True)
        self.chart("system", "kubarr-common", "0.8.2", library=True)
        self.chart("system", "openresty", "1.29.2+5.16", depends="kubarr-common")
        self.chart("system", "kubarr-backend", "0.3.11+4", depends="kubarr-common")
        self.chart("media-server", "audiobookshelf", "2.0.0+3", depends="kubarr-common")
        self.chart("download-client", "deluge", "2.2.0+14", depends="kubarr-common")
        patcher = mock.patch.object(self.gate, "ROOT", self.tree)
        patcher.start()
        self.addCleanup(patcher.stop)

    def chart(self, category, name, version, library=False, depends=None):
        directory = self.tree / category / name
        directory.mkdir(parents=True)
        text = f"apiVersion: v2\nname: {name}\nversion: {version}\n"
        text += "type: library\n" if library else "type: application\n"
        if depends:
            text += textwrap.dedent(f"""\
                dependencies:
                  - name: {depends}
                    version: 0.8.2
                    repository: file://../{depends}
                """)
        (directory / "Chart.yaml").write_text(text)

    def registry(self, *published):
        asked = []

        def lookup(name, version):
            asked.append((name, version))
            return (name, version) in published
        return lookup, asked

    def test_selects_application_charts_whose_version_is_not_published(self):
        lookup, _ = self.registry(("audiobookshelf", "2.0.0+3"), ("deluge", "2.2.0+14"))
        self.assertEqual(self.gate.unpublished_charts(lookup), ["kubarr-backend", "openresty"])

    def test_an_older_build_of_the_same_core_does_not_count_as_published(self):
        lookup, _ = self.registry(("openresty", "1.29.2+5.14"), ("kubarr-backend", "0.3.11+4"),
                                  ("audiobookshelf", "2.0.0+3"), ("deluge", "2.2.0+14"))
        self.assertEqual(self.gate.unpublished_charts(lookup), ["openresty"])

    def test_everything_published_selects_nothing(self):
        lookup, _ = self.registry(("openresty", "1.29.2+5.16"), ("kubarr-backend", "0.3.11+4"),
                                  ("audiobookshelf", "2.0.0+3"), ("deluge", "2.2.0+14"))
        self.assertEqual(self.gate.unpublished_charts(lookup), [])

    def test_a_library_bump_alone_selects_no_consumer(self):
        # kubarr-common 0.8.2 is unpublished, every consumer's own version is.
        lookup, asked = self.registry(("openresty", "1.29.2+5.16"), ("kubarr-backend", "0.3.11+4"),
                                      ("audiobookshelf", "2.0.0+3"), ("deluge", "2.2.0+14"))
        self.assertEqual(self.gate.unpublished_charts(lookup), [])
        self.assertNotIn("kubarr-common", {name for name, _ in asked},
                         "a library chart is never installable, so never looked up")

    def test_the_registry_is_asked_for_the_exact_chart_yaml_version(self):
        lookup, asked = self.registry()
        self.gate.unpublished_charts(lookup)
        self.assertIn(("openresty", "1.29.2+5.16"), asked)
        self.assertIn(("deluge", "2.2.0+14"), asked)

    def test_resolve_targets_drops_the_charts_skipped_by_name(self):
        self.chart("system", "cloudflared", "2026.10.3+3")
        with mock.patch.object(self.gate, "unpublished_charts",
                               return_value=["cloudflared", "openresty"]):
            args = mock.Mock(charts="unpublished", base="origin/main")
            targets, skipped = self.gate.resolve_targets(args)
        self.assertEqual(targets, ["openresty"])
        self.assertIn("cloudflared", skipped)

    def test_all_and_explicit_lists_still_work(self):
        targets, skipped = self.gate.resolve_targets(mock.Mock(charts="all", base="origin/main"))
        self.assertEqual(targets, ["audiobookshelf", "deluge", "kubarr-backend", "openresty"])
        self.assertIn("kubarr-common", skipped)
        targets, _ = self.gate.resolve_targets(mock.Mock(charts="deluge, openresty",
                                                         base="origin/main"))
        self.assertEqual(targets, ["deluge", "openresty"])
        with self.assertRaises(SystemExit):
            self.gate.resolve_targets(mock.Mock(charts="nope", base="origin/main"))


@unittest.skipIf(yaml is None, "PyYAML is required")
class RegistryLookup(unittest.TestCase):
    def setUp(self):
        self.gate = load_gate()

    def answer(self, returncode, stderr=""):
        return mock.Mock(returncode=returncode, stdout="", stderr=stderr)

    def test_published_version_is_found(self):
        with mock.patch.object(self.gate.subprocess, "run", return_value=self.answer(0)) as run:
            self.assertTrue(self.gate.registry_has("openresty", "1.29.2+5.14"))
        command = run.call_args.args[0]
        self.assertEqual(command[-2:], ["--version", "1.29.2+5.14"])
        self.assertEqual(command[3], f"{self.gate.REGISTRY}/openresty")

    def test_not_found_is_unpublished_without_retrying(self):
        missing = self.answer(1, 'Error: failed to perform "FetchReference" on source: '
                                 "ghcr.io/x/openresty:1.29.2_5.16: not found")
        with mock.patch.object(self.gate.subprocess, "run", return_value=missing) as run:
            self.assertFalse(self.gate.registry_has("openresty", "1.29.2+5.16"))
        self.assertEqual(run.call_count, 1)

    def test_a_transport_failure_gates_the_chart_rather_than_waving_it_through(self):
        broken = self.answer(1, "dial tcp: lookup ghcr.io: no such host")
        with mock.patch.object(self.gate.subprocess, "run", return_value=broken) as run, \
                mock.patch.object(self.gate.time, "sleep"):
            self.assertFalse(self.gate.registry_has("openresty", "1.29.2+5.16"))
        self.assertEqual(run.call_count, 3)


@unittest.skipIf(yaml is None, "PyYAML is required")
class UpgradeBaseline(unittest.TestCase):
    """The upgrade starts from the newest published build, not Helm's pick."""

    def setUp(self):
        self.gate = load_gate()

    def test_build_metadata_is_ordered_numerically(self):
        tags = ["1.29.2_5", "1.29.2_5.1", "1.29.2_5.2", "1.29.2_5.10", "1.29.2_5.14", "1.29.2_5.6"]
        self.assertEqual(self.gate.newest_version(tags), "1.29.2+5.14")

    def test_the_core_outranks_the_build(self):
        self.assertEqual(self.gate.newest_version(["2.2.0_14", "2.2.1", "2.2.0_1"]), "2.2.1")
        self.assertEqual(self.gate.newest_version(["16.15.0", "16.15.0_17", "16.14.0_99"]),
                         "16.15.0+17")

    def test_prereleases_and_non_version_tags(self):
        self.assertEqual(self.gate.newest_version(["1.0.0-rc.1", "1.0.0", "sha256-abc.sig"]),
                         "1.0.0")
        self.assertIsNone(self.gate.newest_version(["latest", "sha256-abc.sig"]))


@unittest.skipIf(yaml is None, "PyYAML is required")
class OpenrestyPrerequisites(unittest.TestCase):
    def test_the_gateway_is_installed_after_the_services_it_resolves(self):
        gate = load_gate()
        order = gate.PREREQUISITES["openresty"]
        for needed in ("kubarr-backend", "kubarr-frontend"):
            self.assertIn(needed, order)
        # Not recursive: the backend's own prerequisites come first.
        for needed in gate.PREREQUISITES["kubarr-backend"]:
            self.assertLess(order.index(needed), order.index("kubarr-backend"))


if __name__ == "__main__":
    unittest.main()
