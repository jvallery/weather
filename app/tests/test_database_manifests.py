# SPDX-License-Identifier: MIT
"""Security and data-custody invariants; no database or cluster is started here."""

import importlib.util
import json
import tempfile
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
        self.assertIn(":16.16-standard-trixie-", cluster["spec"]["imageName"])
        self.assertEqual(cluster["spec"]["imagePullSecrets"], [{"name": "zot-origin-cluster-pull"}])
        self.assertFalse(cluster["spec"]["enableSuperuserAccess"])
        self.assertEqual(cluster["spec"]["affinity"]["podAntiAffinityType"], "required")
        self.assertEqual(
            cluster["spec"]["affinity"]["topologyKey"], "topology.vallery.net/proxmox-host"
        )
        self.assertEqual(cluster["spec"]["postgresql"]["parameters"]["archive_timeout"], "300s")
        parameters = cluster["spec"]["postgresql"]["parameters"]
        # Slot-retained WAL must stay bounded, or a stalled slot fills the volume.
        self.assertEqual(parameters["max_slot_wal_keep_size"], "5GB")
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

    def test_wal_archiver_is_the_nas_backed_archive_store(self):
        # weather#334: base backups and WAL go to the NAS-backed Garage bucket, and the
        # retired stores stay declared, keep their history and are never pruned.
        cluster = yaml.safe_load((ROOT / "deploy/k8s/database/cluster.yaml").read_text())
        archive = yaml.safe_load(
            (ROOT / "deploy/k8s/database/object-store-archive.yaml").read_text()
        )
        archiver = [p for p in cluster["spec"]["plugins"] if p.get("isWALArchiver")]
        self.assertEqual(len(archiver), 1)
        self.assertEqual(archiver[0]["parameters"]["barmanObjectName"], "weather-pg-archive")
        self.assertEqual(archiver[0]["parameters"]["serverName"], "weather-pg")
        self.assertEqual(archive["metadata"]["name"], "weather-pg-archive")
        self.assertEqual(
            archive["metadata"]["annotations"]["argocd.argoproj.io/sync-options"],
            "Prune=false,Delete=false",
        )
        conf = archive["spec"]["configuration"]
        self.assertEqual(conf["endpointURL"], "http://garage-hdd.garage-hdd.svc.cluster.local:3900")
        self.assertEqual(conf["destinationPath"], "s3://weather-pg-archive/cnpg")
        self.assertEqual(
            {r["name"] for r in conf["s3Credentials"].values()},
            {"weather-pg-archive-s3-writer"},
        )
        self.assertEqual(archive["spec"]["retentionPolicy"], "7d")
        policy = yaml.safe_load((ROOT / "deploy/k8s/database/recovery-policy.yaml").read_text())
        self.assertEqual(policy["data"]["recovery.pitr_window_days"], "7")
        kustomization = yaml.safe_load(
            (ROOT / "deploy/k8s/database/kustomization.yaml").read_text()
        )
        for name in ("object-store-archive.yaml", "object-store-local.yaml", "object-store.yaml"):
            self.assertIn(name, kustomization["resources"])
        local = yaml.safe_load((ROOT / "deploy/k8s/database/object-store-local.yaml").read_text())
        self.assertEqual(local["metadata"]["name"], "weather-pg-local")
        self.assertEqual(
            local["metadata"]["annotations"]["argocd.argoproj.io/sync-options"],
            "Prune=false,Delete=false",
        )
        self.assertEqual(
            local["metadata"]["annotations"]["storage.vallery.net/lifecycle"], "retired-history"
        )
        self.assertNotIn("retentionPolicy", local["spec"])
        # The sidecar env and resources match, so the repoint changes no instance pod spec.
        self.assertEqual(
            archive["spec"]["instanceSidecarConfiguration"],
            local["spec"]["instanceSidecarConfiguration"],
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
        self.assertIn(":16.16-standard-trixie-", cluster["spec"]["imageName"])
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
        # weather#334: the drill reads the store production archives to, with the
        # read-only key only, and takes the Garage region from the sidecar env.
        production = yaml.safe_load(
            (ROOT / "deploy/k8s/database/object-store-archive.yaml").read_text()
        )["spec"]["configuration"]
        conf = store["spec"]["configuration"]
        self.assertEqual(conf["destinationPath"], production["destinationPath"])
        self.assertEqual(conf["endpointURL"], production["endpointURL"])
        self.assertEqual(
            (conf["destinationPath"], conf["endpointURL"]),
            (drill.SOURCE_PATH, drill.SOURCE_ENDPOINT),
        )
        self.assertEqual(
            {r["name"] for r in conf["s3Credentials"].values()},
            {"weather-pg-archive-s3-reader"},
        )
        self.assertNotIn("region", conf["s3Credentials"])
        self.assertNotIn("retentionPolicy", store["spec"])
        env = {e["name"]: e["value"] for e in store["spec"]["instanceSidecarConfiguration"]["env"]}
        self.assertEqual((env["AWS_DEFAULT_REGION"], env["AWS_REGION"]), ("garage", "garage"))
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
            {p["port"] for rule in policy["egress"] for p in rule["ports"]}, {53, 3900, 6443}
        )
        # No internet egress: every rule names its destination, and 6443 reaches only the
        # API server endpoints and the VIP.
        for rule in policy["egress"]:
            self.assertTrue(rule.get("to"), rule)
        api = next(r for r in policy["egress"] if r["ports"] == [{"port": 6443, "protocol": "TCP"}])
        self.assertEqual(
            {peer["ipBlock"]["cidr"] for peer in api["to"]},
            {"192.168.30.31/32", "192.168.30.32/32", "192.168.30.33/32", "192.168.30.240/32"},
        )
        s3 = next(r for r in policy["egress"] if r["ports"] == [{"port": 3900, "protocol": "TCP"}])
        self.assertEqual(
            s3["to"][0]["namespaceSelector"]["matchLabels"],
            {"kubernetes.io/metadata.name": "garage-hdd"},
        )

    def _preflight_reads(
        self, archiver_store="weather-pg-archive", window=True, server="weather-pg"
    ):
        # The archiver's parameters as cluster.yaml declares them, with the store and server
        # name under test (cluster.yaml also sets the sidecarImage canary).
        cluster_spec = yaml.safe_load((ROOT / "deploy/k8s/database/cluster.yaml").read_text())
        parameters = dict(
            next(p for p in cluster_spec["spec"]["plugins"] if p.get("isWALArchiver"))["parameters"]
        )
        parameters.update(barmanObjectName=archiver_store, serverName=server)
        cluster = {
            "spec": {"plugins": [{"isWALArchiver": True, "parameters": parameters}]},
            "status": {
                "readyInstances": 3,
                "conditions": [{"type": "ContinuousArchiving", "status": "True"}],
            },
        }
        store = yaml.safe_load((ROOT / "deploy/k8s/database/object-store-archive.yaml").read_text())
        if window:
            store["status"] = {
                "serverRecoveryWindow": {
                    "weather-pg": {"firstRecoverabilityPoint": "2026-10-31T11:37:44Z"}
                }
            }
        backups = {
            "items": [
                {"spec": {"cluster": {"name": "weather-pg"}}, "status": {"phase": "completed"}}
            ]
        }

        def read(kind, name):
            return {"cluster": cluster, "objectstore": store}[kind]

        def kubectl(*args, stdin=None):
            if args[:2] == ("get", "backups.postgresql.cnpg.io"):
                return json.dumps(backups)
            self.assertEqual(args[:2], ("get", "secret"))
            self.assertIn(args[2], {drill.WRITER_SECRET, drill.READER_SECRET})
            self.assertTrue(args[4].startswith("go-template={{range"))
            return "AWS_ACCESS_KEY_ID\nAWS_SECRET_ACCESS_KEY"

        return read, kubectl

    def test_preflight_accepts_the_archive_store_and_records_its_recoverability_point(self):
        read, kubectl = self._preflight_reads()
        with patch.object(drill, "read", read), patch.object(drill, "kubectl", kubectl):
            source = drill.preflight()
        self.assertEqual(source["sourceRecoverabilityPoint"], "2026-10-31T11:37:44Z")

    def test_preflight_refuses_a_retired_archiver_or_a_store_without_a_base(self):
        for reads in (
            self._preflight_reads("weather-pg-b2"),
            self._preflight_reads("weather-pg-local"),
            self._preflight_reads(server="weather-pg-restored"),
            self._preflight_reads(window=False),
        ):
            read, kubectl = reads
            with (
                patch.object(drill, "read", read),
                patch.object(drill, "kubectl", kubectl),
                self.assertRaises(RuntimeError),
            ):
                drill.preflight()

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
        self.assertEqual(ports["weather-pg"], {53, 443, 3900, 5432, 6443, 8000})
        self.assertEqual(ports["weather-database-clients"], {53, 5432})
        # The instances reach only the NAS-backed Garage for S3, never the retired store.
        instance = next(p for p in policies if p["metadata"]["name"] == "weather-pg")
        s3 = next(r for r in instance["spec"]["egress"] if r["ports"][0]["port"] == 3900)
        self.assertEqual(
            s3["to"][0]["namespaceSelector"]["matchLabels"],
            {"kubernetes.io/metadata.name": "garage-hdd"},
        )
        # storage-infra#1253: only the change-gate probe pods, only DNS and Garage S3.
        probe = next(p for p in policies if p["metadata"]["name"] == "garage-change-gate-probe")
        self.assertEqual(
            probe["spec"]["podSelector"],
            {"matchLabels": {"app.kubernetes.io/managed-by": "garage-change-gate"}},
        )
        self.assertEqual(probe["spec"]["policyTypes"], ["Egress"])
        self.assertEqual(ports["garage-change-gate-probe"], {53, 3900})

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
        # The hard cap: each wait (60 s connect, 120 s roles) bounds every attempt to 5 s
        # and fails sooner, and each migration statement has its own lock/statement timeout.
        self.assertEqual(migrate["spec"]["activeDeadlineSeconds"], 300)
        self.assertEqual(migrate["spec"]["backoffLimit"], 0)

        def database_secrets(workload: dict) -> set[str]:
            spec = workload["spec"]
            pod = (spec.get("jobTemplate", {}).get("spec", spec))["template"]["spec"]
            names = set()
            for volume in pod.get("volumes", []):
                names.add(volume.get("secret", {}).get("secretName"))
                for source in volume.get("projected", {}).get("sources", []):
                    names.add(source.get("secret", {}).get("name"))
            for container in pod.get("containers", []) + pod.get("initContainers", []):
                for env in container.get("env", []):
                    names.add(env.get("valueFrom", {}).get("secretKeyRef", {}).get("name"))
                for source in container.get("envFrom", []):
                    names.add(source.get("secretRef", {}).get("name"))
            return {n for n in names if n and n.startswith("weather-pg-")}

        # A client is anything labelled with a database role or holding a weather-pg Secret.
        clients = [
            o
            for o in objects
            if o is not migrate
            and (
                "weather.vallery.net/database-role" in o["metadata"].get("labels", {})
                or database_secrets(o)
            )
        ]
        self.assertIn("scheduler", {o["metadata"]["name"] for o in clients})
        for client in clients:
            wave = client["metadata"].get("annotations", {}).get("argocd.argoproj.io/sync-wave")
            self.assertGreaterEqual(int(wave or 0), 2, client["metadata"]["name"])
        scheduler = next(o for o in clients if o["metadata"]["name"] == "scheduler")
        self.assertEqual(scheduler["spec"]["replicas"], 1)
        # Two scheduler versions never overlap, so an old one cannot race a new topic.
        self.assertEqual(scheduler["spec"]["strategy"], {"type": "Recreate"})

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

    def _plan_with_restored_volume(self, pv_claim_uid, pv_name="pv-drill"):
        labels = {drill.LABEL: "proof", "estate.vallery.net/disposable": "true"}
        cluster = {
            "metadata": {"name": "weather-drill-pg-proof", "uid": "cluster-a", "labels": labels}
        }
        pvc = {
            "metadata": {
                "name": "weather-drill-pg-proof-1",
                "uid": "pvc-a",
                "labels": labels,
                "ownerReferences": [{"uid": "cluster-a"}],
            },
            "spec": {"volumeName": pv_name},
        }
        pv = {
            "metadata": {"name": pv_name, "uid": "pv-uid"},
            "spec": {
                "claimRef": {
                    "namespace": "weather",
                    "name": "weather-drill-pg-proof-1",
                    "uid": pv_claim_uid,
                },
                "csi": {"driver": "driver.longhorn.io", "volumeHandle": pv_name},
            },
        }
        volume = {
            "metadata": {"name": pv_name, "uid": "lh-uid"},
            "status": {"kubernetesStatus": {"pvName": pv_name}},
        }
        store = {
            "metadata": {"name": "weather-drill-pg-proof-reader", "uid": "os-a", "labels": labels}
        }
        policy = {
            "metadata": {
                "name": "weather-drill-pg-proof-isolation",
                "uid": "np-a",
                "labels": labels,
            }
        }

        def fake_kubectl(*args, stdin=None):
            if args[:2] == ("get", "pvc"):
                return json.dumps({"items": [pvc]})
            if args[:3] == ("get", "pv", pv_name):
                return json.dumps(pv)
            if args[:3] == ("get", "volumes.longhorn.io", pv_name):
                return json.dumps(volume)
            raise RuntimeError("stop after planning")

        reads = {"cluster": cluster, "objectstore": store, "networkpolicy": policy}
        return fake_kubectl, (lambda kind, name: reads[kind])

    def test_cleanup_plan_binds_the_released_pv_and_longhorn_volume(self):
        fake_kubectl, fake_read = self._plan_with_restored_volume("pvc-a")
        evidence = {"operation": "proof", "verified": True, "clusterUid": "cluster-a"}
        with (
            patch.object(drill, "read", side_effect=fake_read),
            patch.object(drill, "kubectl", side_effect=fake_kubectl),
            tempfile.TemporaryDirectory() as directory,
        ):
            with self.assertRaises(RuntimeError):
                drill.cleanup("proof", evidence, Path(directory) / "evidence.json")
        kinds = [(item["kind"], item["uid"]) for item in evidence["cleanupPlan"]]
        self.assertEqual(kinds[-2:], [("pv", "pv-uid"), ("volumes.longhorn.io", "lh-uid")])
        self.assertLess(kinds.index(("pvc", "pvc-a")), kinds.index(("pv", "pv-uid")))

    def test_cleanup_refuses_a_pv_bound_to_another_claim(self):
        fake_kubectl, fake_read = self._plan_with_restored_volume("someone-else")
        evidence = {"operation": "proof", "verified": True, "clusterUid": "cluster-a"}
        with (
            patch.object(drill, "read", side_effect=fake_read),
            patch.object(drill, "kubectl", side_effect=fake_kubectl),
        ):
            with self.assertRaisesRegex(RuntimeError, "claimRef"):
                drill.cleanup("proof", evidence, Path("unused.json"))
        self.assertNotIn("cleanupPlan", evidence)


if __name__ == "__main__":
    unittest.main()


class ApiManifestTests(unittest.TestCase):
    def test_api_reads_weather_pg_as_its_own_role_after_migrations(self):
        api = next(
            d
            for d in yaml.safe_load_all((ROOT / "deploy/k8s/application.yaml").read_text())
            if d["kind"] == "Deployment" and d["metadata"]["name"] == "api"
        )
        wave = api["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"]
        self.assertGreaterEqual(int(wave), 2)
        pod = api["spec"]["template"]
        self.assertEqual(pod["metadata"]["labels"]["weather.vallery.net/database-role"], "api")
        selector = api["spec"]["selector"]["matchLabels"]
        self.assertNotIn("weather.vallery.net/database-role", selector)
        volumes = {v["name"]: v for v in pod["spec"]["volumes"]}
        self.assertEqual(
            [s["secret"]["name"] for s in volumes["database"]["projected"]["sources"]],
            ["weather-pg-api", "weather-pg-ca"],
        )
        self.assertEqual(volumes["cursor"]["secret"]["secretName"], "weather-api-cursor")
        # The almanac site Secret is a later prerequisite; its absence must never block pods.
        self.assertEqual(volumes["site"]["secret"], {**volumes["site"]["secret"], "optional": True})
        container = pod["spec"]["containers"][0]
        # A database outage must make pods unready, never restart them.
        self.assertEqual(container["startupProbe"]["httpGet"]["path"], "/healthz")
        self.assertEqual(container["livenessProbe"]["httpGet"]["path"], "/healthz")
        self.assertEqual(container["readinessProbe"]["httpGet"]["path"], "/readyz")
