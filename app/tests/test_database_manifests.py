# SPDX-License-Identifier: MIT
"""Security and data-custody invariants; no database or cluster is started here."""

import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("restore_drill", ROOT / "scripts/restore_drill.py")
drill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(drill)


class DatabaseManifestTests(unittest.TestCase):
    def test_role_probe_has_only_the_five_logins_and_ca_without_api_access(self):
        probe = yaml.safe_load((ROOT / "deploy/acceptance/restore/role-logins.yaml").read_text())
        pod = probe["spec"]["template"]["spec"]
        self.assertEqual(pod["imagePullSecrets"], [{"name": "zot-origin-cluster-pull"}])
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertFalse(pod["enableServiceLinks"])
        self.assertTrue(pod["securityContext"]["runAsNonRoot"])
        self.assertEqual(pod["securityContext"]["runAsUser"], 26)
        projected = pod["volumes"][0]["projected"]
        self.assertEqual(
            {source["secret"]["name"] for source in projected["sources"]},
            {
                "weather-pg-" + role
                for role in ("migrator", "ingest", "worker", "api", "editor", "ca")
            },
        )
        container = pod["containers"][0]
        self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])
        self.assertEqual(container["securityContext"]["capabilities"]["drop"], ["ALL"])
        self.assertEqual(
            next(e["value"] for e in container["env"] if e["name"] == "PGSSLMODE"), "verify-full"
        )

    def test_production_retention_and_authority(self):
        cluster = yaml.safe_load((ROOT / "deploy/k8s/database/cluster.yaml").read_text())
        store = yaml.safe_load((ROOT / "deploy/k8s/database/object-store.yaml").read_text())
        for obj in (cluster, store):
            self.assertEqual(
                obj["metadata"]["annotations"]["argocd.argoproj.io/sync-options"],
                "Prune=false,Delete=false",
            )
        self.assertEqual(
            cluster["spec"]["inheritedMetadata"]["annotations"]["argocd.argoproj.io/sync-options"],
            "Prune=false,Delete=false",
        )
        self.assertEqual(cluster["spec"]["instances"], 3)
        self.assertIn(":16.15@sha256:", cluster["spec"]["imageName"])
        self.assertEqual(cluster["spec"]["imagePullSecrets"], [{"name": "zot-origin-cluster-pull"}])
        self.assertFalse(cluster["spec"]["enableSuperuserAccess"])
        self.assertEqual(cluster["spec"]["affinity"]["podAntiAffinityType"], "required")
        self.assertEqual(
            cluster["spec"]["affinity"]["topologyKey"], "topology.vallery.net/proxmox-host"
        )
        self.assertEqual(cluster["spec"]["postgresql"]["parameters"]["archive_timeout"], "300s")
        parameters = cluster["spec"]["postgresql"]["parameters"]
        self.assertEqual(parameters["log_error_verbosity"], "terse")
        self.assertEqual(parameters["log_parameter_max_length"], "0")
        self.assertEqual(parameters["log_parameter_max_length_on_error"], "0")
        self.assertEqual(parameters["log_statement"], "none")
        self.assertEqual(parameters["log_min_error_statement"], "panic")
        self.assertEqual(parameters["log_min_duration_statement"], "-1")
        self.assertEqual(parameters["log_duration"], "off")
        self.assertNotIn("retentionPolicy", store["spec"])
        self.assertNotIn("retentionPolicy", cluster["spec"].get("backup", {}))
        self.assertEqual(
            {r["name"] for r in store["spec"]["configuration"]["s3Credentials"].values()},
            {"weather-pg-backup-b2-writer"},
        )
        roles = cluster["spec"]["managed"]["roles"]
        self.assertEqual(
            {r["name"] for r in roles},
            {"weather_ingest", "weather_worker", "weather_api", "weather_editor"},
        )
        for role in roles:
            self.assertTrue(role["login"])
            self.assertFalse(any(role[k] for k in ("superuser", "createdb", "createrole")))
        self.assertEqual(
            next(r for r in roles if r["name"] == "weather_api")["connectionLimit"], 40
        )

    def test_drill_has_only_reader_and_no_archiver_or_password_rewrite(self):
        objects = list(
            yaml.safe_load_all(drill.manifest("123", "2026-10-24T03:00:00+00:00", "2026-10-25"))
        )
        self.assertEqual({x["kind"] for x in objects}, {"ObjectStore", "Cluster", "NetworkPolicy"})
        for obj in objects:
            self.assertEqual(obj["metadata"]["namespace"], "weather")
            self.assertTrue(obj["metadata"]["name"].startswith("weather-drill-"))
            self.assertEqual(obj["metadata"]["labels"]["weather.vallery.net/restore-drill"], "123")
            self.assertEqual(obj["metadata"]["labels"]["estate.vallery.net/disposable"], "true")
        cluster = next(x for x in objects if x["kind"] == "Cluster")
        store = next(x for x in objects if x["kind"] == "ObjectStore")
        self.assertNotIn("plugins", cluster["spec"])
        self.assertIn(":16.15@sha256:", cluster["spec"]["imageName"])
        self.assertEqual(cluster["spec"]["imagePullSecrets"], [{"name": "zot-origin-cluster-pull"}])
        self.assertNotIn("managed", cluster["spec"])
        self.assertEqual(
            cluster["spec"]["postgresql"]["parameters"]["log_error_verbosity"], "terse"
        )
        self.assertEqual(
            cluster["spec"]["postgresql"]["parameters"]["log_parameter_max_length_on_error"], "0"
        )
        self.assertEqual(
            cluster["spec"]["externalClusters"][0]["plugin"]["parameters"]["serverName"],
            "weather-pg",
        )
        self.assertEqual(
            {r["name"] for r in store["spec"]["configuration"]["s3Credentials"].values()},
            {"weather-pg-backup-b2-reader"},
        )
        policy = next(x for x in objects if x["kind"] == "NetworkPolicy")["spec"]
        self.assertEqual(
            policy["ingress"][0]["from"],
            [
                {
                    "namespaceSelector": {
                        "matchLabels": {"kubernetes.io/metadata.name": "cnpg-system"}
                    }
                }
            ],
        )
        self.assertEqual(
            {p["port"] for rule in policy["egress"] for p in rule["ports"]}, {53, 443, 6443}
        )

    def test_instance_api_egress_includes_endpoint_port_without_widening_clients(self):
        policies = list(
            yaml.safe_load_all((ROOT / "deploy/k8s/database/network-policies.yaml").read_text())
        )
        ports = {
            policy["metadata"]["name"]: {
                p["port"] for rule in policy["spec"]["egress"] for p in rule["ports"]
            }
            for policy in policies
        }
        self.assertEqual(ports["weather-pg"], {53, 443, 5432, 6443, 8000})
        self.assertEqual(ports["weather-database-clients"], {53, 5432})

    def test_migrate_is_a_wave_1_sync_hook_and_db_clients_follow_it(self):
        objects = [
            obj
            for path in sorted((ROOT / "deploy/k8s").rglob("*.yaml"))
            for obj in yaml.safe_load_all(path.read_text())
            if obj and obj.get("kind") in {"Deployment", "StatefulSet", "Job", "CronJob"}
        ]
        migrate = next(o for o in objects if o["metadata"]["name"] == "weather-migrate")
        annotations = migrate["metadata"]["annotations"]
        self.assertEqual(annotations["argocd.argoproj.io/hook"], "Sync")
        self.assertEqual(annotations["argocd.argoproj.io/sync-wave"], "1")
        # First connect (60 s) + role wait (120 s) + bounded statements fit the deadline.
        self.assertEqual(migrate["spec"]["activeDeadlineSeconds"], 300)
        self.assertEqual(migrate["spec"]["backoffLimit"], 0)
        clients = [
            o
            for o in objects
            if "weather.vallery.net/database-role" in o["metadata"].get("labels", {})
            and o is not migrate
        ]
        self.assertIn("scheduler", {o["metadata"]["name"] for o in clients})
        for client in clients:
            wave = client["metadata"].get("annotations", {}).get("argocd.argoproj.io/sync-wave")
            self.assertGreaterEqual(int(wave or 0), 2, client["metadata"]["name"])
        scheduler = next(o for o in clients if o["metadata"]["name"] == "scheduler")
        self.assertEqual(scheduler["spec"]["replicas"], 1)

    def test_operation_cannot_inject_sql_names_or_labels(self):
        for operation in ("bad'name", "../weather-pg", "UPPER", "", "bad-", "-bad", "a" * 32):
            with self.assertRaises(ValueError):
                drill.manifest(operation, "2026-10-24T03:00:00+00:00", "2026-10-25")
        with self.assertRaises(ValueError):
            drill.manifest("valid", "2026-10-24T03:00:00", "2026-10-25")

    def test_unverified_restore_cannot_delete_anything(self):
        with patch.object(drill, "kubectl") as cli, self.assertRaises(RuntimeError):
            drill.cleanup("proof", {"operation": "proof", "verified": False}, Path("unused.json"))
        cli.assert_not_called()

    def test_foreign_pvc_owner_prevents_all_cleanup(self):
        labels = {drill.LABEL: "proof", "estate.vallery.net/disposable": "true"}
        cluster = {
            "metadata": {"name": "weather-drill-pg-proof", "uid": "cluster-a", "labels": labels}
        }
        pvc = {
            "metadata": {
                "name": "weather-drill-pg-proof-1",
                "uid": "pvc-a",
                "labels": labels,
                "ownerReferences": [{"uid": "foreign"}],
            }
        }
        with (
            patch.object(drill, "read", return_value=cluster),
            patch.object(drill, "kubectl", return_value=json.dumps({"items": [pvc]})) as cli,
        ):
            with self.assertRaises(RuntimeError):
                drill.cleanup(
                    "proof",
                    {"operation": "proof", "verified": True, "clusterUid": "cluster-a"},
                    Path("unused.json"),
                )
            self.assertTrue(all(c.args[0] != "delete" for c in cli.call_args_list))

    def test_cleanup_refuses_replacement_uid(self):
        evidence = {
            "operation": "proof",
            "verified": True,
            "cleanupPlan": [{"kind": "pvc", "name": "weather-drill-pg-proof-1", "uid": "original"}],
        }
        replacement = {
            "metadata": {
                "uid": "replacement",
                "labels": {drill.LABEL: "proof", "estate.vallery.net/disposable": "true"},
            }
        }
        with patch.object(
            drill, "kubectl", return_value=json.dumps({"items": [replacement]})
        ) as cli:
            with self.assertRaises(RuntimeError):
                drill.cleanup("proof", evidence, Path("unused.json"))
            self.assertTrue(all(c.args[0] != "delete" for c in cli.call_args_list))

    def test_cleanup_delete_has_exact_uid_and_resource_version(self):
        evidence = {
            "operation": "proof",
            "verified": True,
            "cleanupPlan": [{"kind": "pvc", "name": "weather-drill-pg-proof-1", "uid": "original"}],
        }
        current = {
            "metadata": {
                "uid": "original",
                "resourceVersion": "42",
                "labels": {drill.LABEL: "proof", "estate.vallery.net/disposable": "true"},
            }
        }
        with patch.object(
            drill, "kubectl", side_effect=[json.dumps({"items": [current]}), "{}", '{"items":[]}']
        ) as cli:
            drill.cleanup("proof", evidence, Path("unused.json"))
        deletion = cli.call_args_list[1]
        self.assertEqual(deletion.args[:2], ("delete", "--raw"))
        self.assertEqual(
            json.loads(deletion.kwargs["stdin"])["preconditions"],
            {"uid": "original", "resourceVersion": "42"},
        )
        self.assertEqual(evidence["cleanup"]["remainingPvcCount"], 0)


if __name__ == "__main__":
    unittest.main()
