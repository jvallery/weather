# SPDX-License-Identifier: MIT
"""Bounded foundation acceptance; emits metadata and pass/fail, never private addresses."""

import argparse
import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def kube(*args: str, document: dict[str, Any] | None = None) -> str:
    result = subprocess.run(
        ["kubectl", "--context", "vallery", *args],
        input=None if document is None else json.dumps(document),
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode:
        raise RuntimeError("Kubernetes operation failed: " + args[0])
    return result.stdout


def read(namespace: str, kind: str, name: str = "") -> Any:
    args = ["-n", namespace, "get", kind]
    if name:
        args.append(name)
    return json.loads(kube(*args, "-o", "json"))


def documents(raw: str) -> list[dict[str, Any]]:
    """kubectl printers may emit a List or concatenated JSON resource documents."""
    decoder = json.JSONDecoder()
    result: list[dict[str, Any]] = []
    position = 0
    while position < len(raw):
        if raw[position].isspace():
            position += 1
            continue
        value, position = decoder.raw_decode(raw, position)
        if not isinstance(value, dict):
            raise ValueError("Expected Kubernetes JSON objects")
        result.extend(value["items"] if value.get("kind") == "List" else [value])
    return result


def public_probe(path: str, expected: int) -> None:
    request = urllib.request.Request(
        "https://weather.vallery.net" + path,
        headers={"User-Agent": "weather-foundation-probe/1.0"},
    )
    try:
        response = urllib.request.urlopen(request, timeout=15)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        if response.status != expected:
            raise RuntimeError("Unexpected public response: " + path)
        if expected == 200 and "max-age=" not in response.headers.get(
            "Strict-Transport-Security", ""
        ):
            raise RuntimeError("HSTS missing: " + path)
        response.read(1024 * 1024)


def pod_probe(
    namespace: str, name: str, image: str, program: str, env: list[dict[str, str]]
) -> None:
    manifest = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {
                "estate.vallery.net/disposable": "true",
                "estate.vallery.net/expires": time.strftime("%Y-%m-%d", time.gmtime()),
            },
        },
        "spec": {
            "restartPolicy": "Never",
            "activeDeadlineSeconds": 45,
            "automountServiceAccountToken": False,
            "imagePullSecrets": [{"name": "zot-origin-cluster-pull"}],
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 10001,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [
                {
                    "name": "probe",
                    "image": image,
                    "command": ["python", "-c", program],
                    "env": env,
                    "resources": {
                        "requests": {"cpu": "50m", "memory": "64Mi"},
                        "limits": {"cpu": "250m", "memory": "128Mi"},
                    },
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "readOnlyRootFilesystem": True,
                        "capabilities": {"drop": ["ALL"]},
                    },
                }
            ],
        },
    }
    kube("create", "-f", "-", document=manifest)
    try:
        # A completed probe must be observed before its disposable object is removed.
        end = time.monotonic() + 55
        while time.monotonic() < end:
            phase = read(namespace, "pod", name)["status"].get("phase")
            if phase in {"Succeeded", "Failed"}:
                if phase != "Succeeded":
                    raise RuntimeError("Probe failed: " + name)
                return
            time.sleep(1)
        raise RuntimeError("Probe did not reach terminal state: " + name)
    finally:
        kube("-n", namespace, "delete", "pod", name, "--wait=false")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", required=True, help="exact deployed promotion SHA")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--network", action="store_true", help="create two disposable probe pods")
    args = parser.parse_args()
    app = read("argocd", "application", "weather")
    status = app["status"]
    assert status["sync"]["revision"] == args.revision, "Argo revision differs"
    assert status["sync"]["status"] == "Synced", "Argo not Synced"
    assert status["health"]["status"] == "Healthy", "Argo not Healthy"
    ns = read("weather", "namespace", "weather")
    assert ns["metadata"]["labels"]["pod-security.kubernetes.io/enforce"] == "restricted"
    read("weather", "resourcequota", "weather-budget")
    read("weather", "limitrange", "weather-defaults")
    deployments = {name: read("weather", "deployment", name) for name in ("api", "web")}
    rendered = documents(kube("create", "--dry-run=client", "-k", "deploy/k8s", "-o", "json"))
    desired = {
        item["metadata"]["name"]: item["spec"]["template"]["spec"]["containers"][0]["image"]
        for item in rendered
        if item["kind"] == "Deployment" and item["metadata"]["name"] in deployments
    }
    digests = {}
    for name, deployment in deployments.items():
        assert deployment["spec"]["replicas"] == 2
        assert deployment["status"]["observedGeneration"] == deployment["metadata"]["generation"]
        assert deployment["status"].get("updatedReplicas", 0) == 2
        assert deployment["status"].get("readyReplicas", 0) == 2
        assert deployment["status"].get("availableReplicas", 0) == 2
        image = deployment["spec"]["template"]["spec"]["containers"][0]["image"]
        assert image == desired[name], "Running template differs from source pin"
        assert "@sha256:" in image and image.startswith("registry.vallery.net/"), "Unpinned image"
        digests[name] = image.split("@", 1)[1]
    pods = read("weather", "pods")["items"]
    for pod in pods:
        assert pod["spec"].get("automountServiceAccountToken") is False
        assert not any(
            v["name"].startswith("kube-api-access") for v in pod["spec"].get("volumes", [])
        )
        name = pod["metadata"].get("labels", {}).get("app.kubernetes.io/component")
        if name in deployments and pod["status"]["phase"] == "Running":
            for container in pod["status"].get("containerStatuses", []):
                assert container["ready"]
                assert container["imageID"].endswith(digests[name]), "Running digest differs"
    public_probe("/", 200)
    public_probe("/api/healthz", 200)
    public_probe("/metrics", 404)
    if args.network:
        api_ip = read("weather", "service", "api")["spec"]["clusterIP"]
        web_ip = read("weather", "service", "web")["spec"]["clusterIP"]
        suffix = str(time.time_ns())
        pod_probe(
            "weather", "weather-deny-" + suffix, desired["api"],
            "import os,socket,sys\n"
            "try: socket.create_connection((os.environ['API'],80),3)\n"
            "except OSError: sys.exit(0)\n"
            "sys.exit(1)",
            [{"name": "API", "value": api_ip}],
        )
        pod_probe(
            "observability", "weather-metrics-" + suffix, desired["api"],
            "import os,urllib.request\n"
            "for key,port,path in [('API',80,'/readyz'),('API',9000,'/metrics'),"
            "('WEB',9000,'/metrics')]:\n"
            " r=urllib.request.urlopen('http://'+os.environ[key]+':'+str(port)+path,timeout=5)\n"
            " assert r.status==200\n"
            " body=r.read(1048576)\n"
            " assert path!='/metrics' or b'# HELP' in body\n",
            [{"name": "API", "value": api_ip}, {"name": "WEB", "value": web_ip}],
        )
    receipt = {
        "revision": args.revision,
        "argo": "Synced/Healthy",
        "images": desired,
        "public_probes": "passed",
        "tokenless": True,
        "network_probes": "passed" if args.network else "not run",
    }
    args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
