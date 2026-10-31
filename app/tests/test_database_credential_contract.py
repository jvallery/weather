# SPDX-License-Identifier: MIT
"""The local WAL store selects only gate-created Secret keys the contract attests (si#1253)."""

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DATABASE = ROOT / "deploy/k8s/database"
CONTRACT_PATH = DATABASE / "weather-pg-backup-credential-contract.yaml"
STORE_PATH = DATABASE / "object-store-local.yaml"
DRILL_PATH = ROOT / "deploy/acceptance/restore/drill.yaml"
UID = r"^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$"


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def blockers(contract: dict, store: dict) -> list[str]:
    """Every reason the store's credential selectors are not backed by the contract."""
    rows = {row["secret"]: row for row in contract.get("credentials") or []}
    found = []
    selectors = store["spec"]["configuration"].get("s3Credentials") or {}
    for field, selector in selectors.items():
        row = rows.get(selector["name"])
        if row is None:
            found.append(f"{field}: Secret {selector['name']} is not in the contract")
            continue
        if row["namespace"] != store["metadata"]["namespace"]:
            found.append(f"{field}: Secret {selector['name']} is in another namespace")
        if selector["key"] not in (row.get("observed") or {}).get("keys", []):
            found.append(f"{field}: key {selector['key']} not observed in {selector['name']}")
        if selector["key"] not in row.get("required_keys", []):
            found.append(f"{field}: key {selector['key']} not required by {selector['name']}")
    return found


class CredentialContract(unittest.TestCase):
    def setUp(self):
        self.contract = load(CONTRACT_PATH)
        self.store = load(STORE_PATH)

    def test_contract_is_not_a_kubernetes_manifest(self):
        self.assertNotIn("apiVersion", self.contract)
        self.assertNotIn("kind", self.contract)
        kustomization = load(DATABASE / "kustomization.yaml")
        rendered = [*kustomization.get("resources", []), *kustomization.get("patches", [])]
        self.assertNotIn(CONTRACT_PATH.name, [str(r) for r in rendered])

    def test_store_selectors_are_attested(self):
        self.assertEqual(blockers(self.contract, self.store), [])

    def test_store_uses_the_writer_and_the_contract_splits_roles(self):
        rows = {row["role"]: row for row in self.contract["credentials"]}
        self.assertEqual(set(rows), {"writer", "reader"})
        self.assertEqual(rows["writer"]["permissions"], "read-write")
        self.assertEqual(rows["reader"]["permissions"], "read-only")
        self.assertEqual(
            rows["writer"]["consumed_by"], "deploy/k8s/database/object-store-local.yaml"
        )
        selectors = self.store["spec"]["configuration"]["s3Credentials"].values()
        selected = {s["name"] for s in selectors}
        self.assertEqual(selected, {rows["writer"]["secret"]})
        for row in rows.values():
            self.assertEqual(row["bucket"], "weather-pg")
            self.assertEqual(row["provisioned"]["change_id"], "weather-pg-availability-garage-1253")
            self.assertEqual(row["observed"]["change_id"], row["provisioned"]["change_id"])
            self.assertRegex(row["provisioned"]["uid"], UID)
            self.assertRegex(row["observed"]["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_the_drill_reads_with_the_attested_reader_only(self):
        # storage-infra#1253 step 7: a drill can never archive into s3://weather-pg/cnpg.
        rows = {row["role"]: row for row in self.contract["credentials"]}
        self.assertEqual(rows["reader"]["consumed_by"], "deploy/acceptance/restore/drill.yaml")
        drill_store = next(
            doc
            for doc in yaml.safe_load_all(DRILL_PATH.read_text())
            if doc["kind"] == "ObjectStore"
        )
        self.assertEqual(blockers(self.contract, drill_store), [])
        selected = {
            s["name"] for s in drill_store["spec"]["configuration"]["s3Credentials"].values()
        }
        self.assertEqual(selected, {rows["reader"]["secret"]})

    def test_region_comes_from_sidecar_env_not_a_secret(self):
        # The gate writes no region key; selecting one would break WAL archiving at runtime.
        self.assertNotIn("region", self.store["spec"]["configuration"]["s3Credentials"])
        env = {
            e["name"]: e.get("value")
            for e in self.store["spec"]["instanceSidecarConfiguration"]["env"]
        }
        region = self.contract["region"]["value"]
        self.assertEqual(region, "garage")
        self.assertEqual(env["AWS_DEFAULT_REGION"], region)
        self.assertEqual(env["AWS_REGION"], region)
        for e in self.store["spec"]["instanceSidecarConfiguration"]["env"]:
            self.assertNotIn("valueFrom", e)

    def test_contract_carries_no_secret_material(self):
        text = CONTRACT_PATH.read_text()
        # Garage key ids are GK + 24 hex; secrets are 64 hex. Neither may appear.
        self.assertIsNone(re.search(r"\bGK[0-9a-f]{24}\b", text))
        self.assertIsNone(re.search(r"\b[0-9a-f]{64}\b", text))
        self.assertNotIn("{{index", text.replace(" ", ""))
        self.assertNotIn("{{len", text.replace(" ", ""))


class InterlockActuallyFails(unittest.TestCase):
    def setUp(self):
        self.contract = load(CONTRACT_PATH)
        self.store = load(STORE_PATH)

    def test_a_region_key_the_gate_never_wrote_is_blocked(self):
        store = load(STORE_PATH)
        store["spec"]["configuration"]["s3Credentials"]["region"] = {
            "name": "weather-pg-backup-s3-writer-k3s",
            "key": "AWS_DEFAULT_REGION",
        }
        found = blockers(self.contract, store)
        self.assertTrue(found)
        self.assertTrue(all("AWS_DEFAULT_REGION" in reason for reason in found))

    def test_an_unknown_secret_is_blocked(self):
        store = load(STORE_PATH)
        store["spec"]["configuration"]["s3Credentials"]["accessKeyId"]["name"] = "other"
        self.assertEqual(len(blockers(self.contract, store)), 1)


if __name__ == "__main__":
    unittest.main()
