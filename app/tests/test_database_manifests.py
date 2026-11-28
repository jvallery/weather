# SPDX-License-Identifier: MIT
"""Security and data-custody invariants; no database or cluster is started here."""

import datetime as dt
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "restore_drill", ROOT / "deploy/k8s/restore-drill/restore_drill.py"
)
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

    def _copy(self, owner=None):
        return yaml.safe_load(drill.manifest("proof-1", "2026-11-30", owner))

    def test_drill_restores_from_the_b2_custody_copy_with_the_reader_only(self):
        store = yaml.safe_load((ROOT / "deploy/acceptance/restore/drill.yaml").read_text())
        self.assertEqual(store["kind"], "ObjectStore")
        self.assertEqual(store["metadata"]["name"], drill.READER_STORE)
        # Argo CD keeps the store; it carries no drill label, so a drill sweep never matches it.
        self.assertNotIn(drill.LABEL, store["metadata"].get("labels", {}))
        conf = store["spec"]["configuration"]
        self.assertEqual(conf["destinationPath"], drill.SOURCE_PATH)
        self.assertTrue(
            conf["destinationPath"].startswith("s3://object-custody-vallery/object-custody/")
        )
        self.assertIn("backblazeb2.com", conf["endpointURL"])
        self.assertEqual({r["name"] for r in conf["s3Credentials"].values()}, {drill.READER_SECRET})
        self.assertNotIn("retentionPolicy", store["spec"])
        self.assertNotIn("garage", (ROOT / "deploy/acceptance/restore/drill.yaml").read_text())
        cluster = self._copy()
        self.assertEqual(cluster["kind"], "Cluster")
        self.assertEqual(cluster["metadata"]["name"], drill.COPY)
        self.assertEqual(cluster["metadata"]["ownerReferences"], [])
        self.assertEqual(cluster["metadata"]["labels"][drill.LABEL], "proof-1")
        self.assertNotIn("annotations", cluster["metadata"])
        for absent in ("plugins", "backup", "managed", "env", "envFrom", "projectedVolumeTemplate"):
            self.assertNotIn(absent, cluster["spec"])
        self.assertEqual(set(cluster["spec"]["bootstrap"]["recovery"]), {"source"})
        self.assertIn(":16.16-standard-trixie-", cluster["spec"]["imageName"])
        self.assertEqual(
            cluster["spec"]["storage"]["storageClass"], "longhorn-v1-bulk-disposable-r1"
        )
        (external,) = cluster["spec"]["externalClusters"]
        self.assertEqual(external["name"], cluster["spec"]["bootstrap"]["recovery"]["source"])
        self.assertEqual(
            external["plugin"]["parameters"],
            {"barmanObjectName": drill.READER_STORE, "serverName": "weather-pg"},
        )
        self.assertEqual(set(cluster["spec"]["inheritedMetadata"]), {"labels"})
        self.assertEqual(cluster["spec"]["postgresql"]["parameters"]["log_statement"], "none")

    def test_drill_copy_is_owned_by_its_job(self):
        owner = {"apiVersion": "batch/v1", "kind": "Job", "name": "j", "uid": "u"}
        self.assertEqual(self._copy(owner)["metadata"]["ownerReferences"], [owner])

    def test_operation_cannot_inject_names_or_labels(self):
        for job in ("weather-pg-restore-drill-UPPER", "bad'name", "a" * 25, "x-", ""):
            with self.assertRaises(ValueError):
                drill.operation_from(job)
        self.assertEqual(drill.operation_from("weather-pg-restore-drill-29412345"), "29412345")
        self.assertEqual(drill.operation_from("weather-pg-restore-drill-proof-1"), "proof-1")
        with self.assertRaises(ValueError):
            drill.manifest("../weather-pg", "2026-11-30", None)

    def test_drill_creates_only_its_one_fixed_name(self):
        # A template edited to another (production or look-alike) name is refused before any
        # request; the admission policy weather-restore-drill-boundary refuses it in the cluster.
        for name in ("weather-pg", "weather-pg-restore-drill-copy2"):
            text = drill.TEMPLATE.read_text().replace(
                "  name: weather-pg-restore-drill-copy\n", f"  name: {name}\n"
            )
            with (
                patch.object(drill, "TEMPLATE", Mock(read_text=Mock(return_value=text))),
                self.assertRaises(ValueError),
            ):
                drill.manifest("proof-1", "2026-11-30", None)

    def test_recovery_log_and_counts_decide_the_result(self):
        line = (
            '{"msg":"last completed transaction was at log time 2026-11-20 09:12:01.5+00"}\n'
            '{"msg":"last completed transaction was at log time 2026-11-20 09:13:02.25+00"}'
        )
        self.assertEqual(
            drill.last_transaction(line),
            dt.datetime(2026, 11, 20, 9, 13, 2, 250000, tzinfo=dt.UTC),
        )
        self.assertIsNone(drill.last_transaction("redo done"))
        rows = {"public_api.station": 1, "public_api.obs_st": 1440, "public_api.rapid_wind": 28800}
        self.assertEqual(drill.compare(rows, dict(rows)), [])
        self.assertEqual(len(drill.compare(rows, {**rows, "public_api.obs_st": 1439})), 1)
        self.assertEqual(len(drill.compare({}, rows)), 3)

    def test_cronjob_runs_the_drill_weekly(self):
        base = ROOT / "deploy/k8s/restore-drill"
        kustomization = yaml.safe_load((base / "kustomization.yaml").read_text())
        root = yaml.safe_load((ROOT / "deploy/k8s/kustomization.yaml").read_text())
        self.assertIn("restore-drill", root["resources"])
        self.assertIn("drill.yaml", kustomization["resources"])
        self.assertEqual(
            kustomization["configMapGenerator"][0]["files"], ["restore_drill.py", "copy.yaml"]
        )
        self.assertEqual(
            (ROOT / "deploy/acceptance/restore/drill.yaml").resolve(),
            (base / "drill.yaml").resolve(),
        )
        cron = yaml.safe_load((base / "cronjob.yaml").read_text())
        self.assertEqual(cron["metadata"]["name"], "weather-pg-restore-drill")
        self.assertFalse(cron["spec"]["suspend"])
        self.assertEqual(cron["spec"]["concurrencyPolicy"], "Forbid")
        minute, hour, dom, month, dow = cron["spec"]["schedule"].split()
        self.assertEqual((dom, month), ("*", "*"))
        self.assertTrue(dow.isdigit() and minute.isdigit() and hour.isdigit())
        job = cron["spec"]["jobTemplate"]["spec"]
        self.assertEqual(job["backoffLimit"], 0)
        self.assertGreater(job["ttlSecondsAfterFinished"], 0)
        pod = job["template"]["spec"]
        self.assertEqual(pod["serviceAccountName"], "weather-pg-restore-drill")
        self.assertEqual(
            [s["secret"]["name"] for s in pod["volumes"][1]["projected"]["sources"]],
            ["weather-pg-api", "weather-pg-ca"],
        )

    def test_role_has_no_unnamed_write_or_read_of_clusters_and_nothing_on_stores(self):
        rbac = list(yaml.safe_load_all((ROOT / "deploy/k8s/restore-drill/rbac.yaml").read_text()))
        self.assertEqual({o["kind"] for o in rbac}, {"ServiceAccount", "Role", "RoleBinding"})
        role = next(o for o in rbac if o["kind"] == "Role")
        for rule in role["rules"]:
            self.assertNotIn("*", rule["verbs"])
            self.assertNotIn("*", rule["resources"])
            for resource in ("secrets", "objectstores", "persistentvolumes", "rolebindings"):
                self.assertNotIn(resource, rule["resources"])
            # Every verb that changes or deletes an object is bound to a name, except create,
            # which RBAC cannot bind and the admission policy bounds.
            if set(rule["verbs"]) & {"delete", "deletecollection", "update", "patch"}:
                self.assertEqual(rule.get("resourceNames"), [drill.COPY], rule)
                self.assertNotIn("deletecollection", rule["verbs"])
            if "clusters" in rule["resources"] and rule["verbs"] != ["create"]:
                self.assertEqual(rule.get("resourceNames"), [drill.COPY], rule)
        verbs = {(r, v) for rule in role["rules"] for r in rule["resources"] for v in rule["verbs"]}
        self.assertEqual(
            {(r, v) for r, v in verbs if v not in ("get", "list")},
            {("clusters", "create"), ("clusters", "delete")},
        )

    def test_drill_isolation_admits_only_b2_tls_the_api_port_and_the_runner(self):
        text = (ROOT / "deploy/k8s/restore-drill/network-policy.yaml").read_text()
        policies = {p["metadata"]["name"]: p["spec"] for p in yaml.safe_load_all(text)}
        instance = policies["weather-pg-restore-drill-instance"]
        self.assertEqual(
            {p["port"] for rule in instance["egress"] for p in rule["ports"]}, {53, 443, 6443}
        )
        public = next(r for r in instance["egress"] if r["ports"][0]["port"] == 443)
        self.assertIn("192.168.0.0/16", public["to"][0]["ipBlock"]["except"])
        # The API endpoint port is port-only, as in the production policy: no private address
        # is published, and every ipBlock in these policies is the public-only one above.
        for spec in policies.values():
            for rule in spec["egress"]:
                for peer in rule.get("to", []):
                    if "ipBlock" in peer:
                        self.assertEqual(peer["ipBlock"]["cidr"], "0.0.0.0/0")
        # The media drill's Job pods carry the drill label but no cnpg.io/cluster label.
        self.assertIn(
            {"key": "cnpg.io/cluster", "operator": "Exists"},
            instance["podSelector"]["matchExpressions"],
        )
        runner = policies["weather-pg-restore-drill-runner"]
        self.assertEqual(runner["ingress"], [])
        self.assertEqual(
            {p["port"] for rule in runner["egress"] for p in rule["ports"]}, {53, 5432, 6443}
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
