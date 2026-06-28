from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DeploymentContractsTest(unittest.TestCase):
    def test_container_includes_governed_knowledge_corpus(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("COPY docs ./docs", dockerfile)
        self.assertIn("COPY PROJECT_PLAN.md ./", dockerfile)
        self.assertIn("http://127.0.0.1:8000/live", dockerfile)

    def test_kubernetes_preserves_single_writer_and_probe_contracts(
        self,
    ) -> None:
        deployment = (
            ROOT / "deploy/kubernetes/base/deployment.yaml"
        ).read_text(encoding="utf-8")
        network_policy = (
            ROOT / "deploy/kubernetes/base/network-policy.yaml"
        ).read_text(encoding="utf-8")
        config_map = (
            ROOT / "deploy/kubernetes/base/config-map.yaml"
        ).read_text(encoding="utf-8")
        production = (
            ROOT
            / "deploy/kubernetes/overlays/production/kustomization.yaml"
        ).read_text(encoding="utf-8")

        self.assertIn("replicas: 1", deployment)
        self.assertIn("type: Recreate", deployment)
        self.assertIn("path: /live", deployment)
        self.assertIn("path: /ready", deployment)
        self.assertIn("readOnlyRootFilesystem: true", deployment)
        self.assertIn("kind: NetworkPolicy", network_policy)
        self.assertIn("port: 4318", network_policy)
        self.assertIn('IIMC_OTEL_ENABLED: "true"', config_map)
        self.assertIn(
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
            config_map,
        )
        self.assertIn("ghcr.io/replace-me/iimc-platform", production)

    def test_compose_has_real_trace_pipeline(self) -> None:
        compose = (ROOT / "docker-compose.yml").read_text(
            encoding="utf-8"
        )
        collector = (
            ROOT / "deploy/observability/otel-collector.yaml"
        ).read_text(encoding="utf-8")

        self.assertIn("otel-collector:", compose)
        self.assertIn("jaeger:", compose)
        self.assertIn("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", compose)
        self.assertIn("memory_limiter:", collector)
        self.assertIn("batch:", collector)
        self.assertIn("otlp/jaeger:", collector)


if __name__ == "__main__":
    unittest.main()
