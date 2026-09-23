import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT


class DeploymentContractTests(unittest.TestCase):
    def test_dockerfile_builds_from_hostinger_image_and_runs_patch_tests(self):
        path = PROJECT / "Dockerfile"
        self.assertTrue(path.exists(), "Dockerfile is missing")
        text = path.read_text(encoding="utf-8")
        self.assertIn("ARG BASE_IMAGE=ghcr.io/hostinger/hvps-hermes-agent:latest", text)
        self.assertIn("FROM ${BASE_IMAGE}", text)
        self.assertIn("runtime_footer_patch.py", text)
        self.assertIn("verify_installed.py", text)
        self.assertIn("https://github.com/mate5630-debug/hermes-runtime-footer", text)
        self.assertIn("USER root", text)

    def test_workflow_builds_and_publishes_derived_image(self):
        path = ROOT / ".github/workflows/build.yml"
        self.assertTrue(path.exists(), "GitHub Actions workflow is missing")
        text = path.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("schedule:", text)
        self.assertIn("packages: write", text)
        self.assertIn("docker/build-push-action", text)
        self.assertIn("ghcr.io/${{ github.repository_owner }}/hermes-runtime-footer-public", text)

    def test_hostinger_compose_override_preserves_data_volume_and_uses_derived_image(self):
        path = PROJECT / "hostinger-compose.override.yml"
        self.assertTrue(path.exists(), "Hostinger compose override is missing")
        text = path.read_text(encoding="utf-8")
        self.assertIn("ghcr.io/mate5630-debug/hermes-runtime-footer-public:latest", text)
        self.assertIn("/opt/data", text)
        self.assertIn("pull_policy: always", text)


if __name__ == "__main__":
    unittest.main()
