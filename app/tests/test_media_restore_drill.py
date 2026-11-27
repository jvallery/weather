# SPDX-License-Identifier: MIT
"""weather#77 media drill manifest invariants; no cluster is contacted."""

import importlib.util
import json
import unittest
from argparse import Namespace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "media_drill", ROOT / "scripts/media_restore_drill.py"
)
drill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(drill)
APP = "registry.vallery.net/jvallery/weather-app@sha256:" + "a" * 64
WEB = "registry.vallery.net/jvallery/weather-web@sha256:" + "b" * 64


class MediaDrillManifestTests(unittest.TestCase):
    def rendered(self):
        with patch.object(drill, "live_image", side_effect=lambda d, c: APP if d == "api" else WEB):
            name, text = drill.render("test01", "2026-10-29")
        policy, job = yaml.safe_load_all(text)
        # A Job, never a bare Pod (weather#328): one attempt, a deadline and a TTL.
        self.assertEqual((job["apiVersion"], job["kind"]), ("batch/v1", "Job"))
        self.assertEqual(job["spec"]["backoffLimit"], 0)
        self.assertEqual(job["spec"]["activeDeadlineSeconds"], 10800)
        self.assertGreater(job["spec"]["ttlSecondsAfterFinished"], 0)
        # The NetworkPolicy selects the pod by the drill label on the pod template.
        self.assertEqual(
            job["spec"]["template"]["metadata"]["labels"][drill.LABEL], "test01"
        )
        return name, policy, job, job["spec"]["template"]

    def test_labels_and_no_api_access(self):
        name, policy, job, pod = self.rendered()
        for obj in (policy, job):
            self.assertEqual(obj["metadata"]["namespace"], "weather")
            self.assertEqual(obj["metadata"]["labels"][drill.LABEL], "test01")
            self.assertEqual(obj["metadata"]["labels"]["estate.vallery.net/disposable"], "true")
        spec = pod["spec"]
        self.assertFalse(spec["automountServiceAccountToken"])
        self.assertFalse(spec["enableServiceLinks"])
        self.assertEqual(spec["restartPolicy"], "Never")
        self.assertTrue(spec["securityContext"]["runAsNonRoot"])
        # The app's fixed database name resolves to the pod's own catalog copy.
        self.assertEqual(spec["hostAliases"], [{"ip": "127.0.0.1", "hostnames": ["weather-pg-rw"]}])

    def test_egress_is_dns_and_public_https_only(self):
        _, policy, _, _ = self.rendered()
        self.assertEqual(policy["spec"]["policyTypes"], ["Ingress", "Egress"])
        self.assertNotIn("ingress", policy["spec"])
        dns, https = policy["spec"]["egress"]
        self.assertEqual({p["port"] for p in dns["ports"]}, {53})
        self.assertEqual([p["port"] for p in https["ports"]], [443])
        block = https["to"][0]["ipBlock"]
        self.assertEqual(block["cidr"], "0.0.0.0/0")
        self.assertTrue({"10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"} <= set(block["except"]))

    def test_readers_by_name_only_and_api_never_holds_originals(self):
        _, _, _, pod = self.rendered()
        containers = pod["spec"]["initContainers"] + pod["spec"]["containers"]
        secrets = {
            (c["name"], e["valueFrom"]["secretKeyRef"]["name"])
            for c in containers
            for e in c.get("env", [])
            if "valueFrom" in e
        }
        self.assertEqual(
            secrets,
            {
                ("catalog-init", "weather-pg-backup-b2-reader"),
                ("driver", "weather-custody-restore-reader"),
            },
        )
        self.assertFalse(any(v.get("secret") for v in pod["spec"]["volumes"]))
        api = next(c for c in containers if c["name"] == "api")
        mounts = {m["mountPath"]: m.get("subPath") for m in api["volumeMounts"]}
        self.assertEqual(mounts["/var/run/weather-garage/weather-media"], "garage/weather-media")
        self.assertFalse(any("originals" in (m.get("subPath") or "") for m in api["volumeMounts"]))
        for c in containers:
            self.assertIn("@sha256:", c["image"])
            self.assertFalse(c["securityContext"]["allowPrivilegeEscalation"])
            self.assertTrue(c["securityContext"]["readOnlyRootFilesystem"])


class MediaDrillVerdictTests(unittest.TestCase):
    def test_requires_unavailable_page_state_and_withheld_404(self):
        good = {
            "day_page_status": 200,
            "day_page_has_media": True,
            "day_page_mentions_unavailable": True,
            "media_restored_still_status": 200,
            "media_withheld_status": 404,
            "media_original_status": 404,
        }
        for changes, expected in (
            ({}, True),
            ({"day_page_mentions_unavailable": False}, False),
            ({"media_withheld_status": 200}, False),
            ({"media_withheld_status": 503}, False),
        ):
            with self.subTest(changes=changes), TemporaryDirectory() as tmp:
                evidence = Path(tmp) / "receipt.json"
                args = Namespace(
                    operation="test01", day="2026-10-29", plan=False, evidence=evidence
                )
                pod = '{"metadata": {"uid": "synthetic"}, "spec": {"containers": []}}'
                with (
                    patch.object(drill, "render", return_value=("synthetic", "")),
                    patch.object(drill, "rpo_candidates", return_value=[]),
                    patch.object(drill, "configmap", return_value=""),
                    patch.object(drill, "kubectl", return_value=pod),
                    patch.object(drill, "job_pod", return_value="synthetic-pod"),
                    patch.object(
                        drill, "wait_receipt", return_value={"result": "pass", "start_epoch": 1}
                    ),
                    patch.object(drill, "probes", return_value={**good, **changes}),
                    patch("builtins.print"),
                ):
                    drill.run(args)
                self.assertIs(json.loads(evidence.read_text())["verified"], expected)


if __name__ == "__main__":
    unittest.main()
