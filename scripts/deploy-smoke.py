#!/usr/bin/env python3
"""Exercise the real Kubernetes overlay in an isolated Linux amd64 kind cluster.

The cluster, kubeconfig and generated credentials belong only to this smoke
run. Nothing uses the caller's default Kubernetes context. Logs contain only
safe status and aggregate verification results, never HTTP bodies or secrets.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import urllib.request
import uuid


ROOT = Path(__file__).resolve().parents[1]
KIND_VERSION = "v0.33.0"
KIND_SHA256 = "aee6151561422756b764a4ae28e7f44cda5af5a9eead3cc9985112b1de8d8e0d"
KUBECTL_VERSION = "v1.34.11"
KUBECTL_SHA256 = "8efbb9435132a190920eb65a47a8c1ecf755ad85ab57a600c9bedbab460bb7a8"
NODE_IMAGE = "kindest/node:v1.34.11@sha256:44e222ee2132dab25ff87301682f89eb82c7880ea3a1bf543bfe9708fd08d67d"
LOCAL_IMAGE = "quest-deploy:test"
CLUSTER_PATTERN = re.compile(r"quest-deploy-[0-9a-f]{12}\Z")
RESULT_PREFIX = "QUEST_DEPLOY_SMOKE_JSON:"


class SmokeError(RuntimeError):
    pass


def run(command, label, *, environment, body=None, timeout=300):
    """Never echo commands containing private paths, input or remote output."""
    try:
        result = subprocess.run(command, input=body, capture_output=True, text=True,
                                env=environment, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise SmokeError(label + " timed out") from None
    if result.returncode:
        raise SmokeError(label + " failed (exit " + str(result.returncode) + ")")
    return result.stdout


def download_binary(url, expected, destination):
    with urllib.request.urlopen(url, timeout=60) as response:
        payload = response.read()
    if hashlib.sha256(payload).hexdigest() != expected:
        raise SmokeError("Checksum mismatch for " + destination.name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    destination.chmod(0o755)


def save_state(path, state):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as output:
        json.dump(state, output)
    path.chmod(0o600)


def validate_state(path, state):
    directory = Path(state["directory"]).resolve()
    if (not CLUSTER_PATTERN.fullmatch(state["cluster"])
            or directory.parent != path.parent.resolve()
            or not directory.name.startswith("quest-deploy-smoke-")):
        raise SmokeError("Refusing cleanup of an unrecognized smoke cluster")
    if Path(state["kubeconfig"]).resolve() != directory / "kubeconfig":
        raise SmokeError("Refusing cleanup with a different kubeconfig")
    return directory


def environment_for(state):
    environment = os.environ.copy()
    environment["KUBECONFIG"] = state["kubeconfig"]
    environment["KIND_EXPERIMENTAL_PROVIDER"] = "docker"
    return environment


def cleanup(path):
    if not path.exists():
        return
    state = json.loads(path.read_text())
    directory = validate_state(path, state)
    # Remove the local plaintext copy even if deleting a partial kind cluster
    # fails. Keep only the tooling/kubeconfig for the CI always-step retry.
    (directory / "ctfd.env").unlink(missing_ok=True)
    kind = directory / "bin" / "kind"
    if state.get("creation_attempted"):
        if not kind.is_file():
            raise SmokeError("Cannot clean smoke cluster without its verified kind binary")
        run([str(kind), "delete", "cluster", "--name", state["cluster"],
             "--kubeconfig", state["kubeconfig"]], "Smoke cluster cleanup",
            environment=environment_for(state), timeout=120)
    shutil.rmtree(directory)
    path.unlink()


def probe_script(password):
    # Credentials travel only in kubectl exec stdin; they are never arguments,
    # environment variables in the web container, logs or retained artifacts.
    return '''import contextlib
import hashlib
import io
import json
import os
from CTFd import create_app
from CTFd.models import Users, Teams, Challenges, Solves
from CTFd.plugins.open_tech_quest.models import QuestEvent, QuestRoom
password = PASSWORD_LITERAL
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    assert "CTFD_ADMIN_PASSWORD" not in os.environ, "Admin password leaked into the web container environment"
    app = create_app()
    assert app.config["SESSION_COOKIE_SECURE"] is True, "Secure session cookie configuration was lost"
    with app.app_context():
        assert Users.query.count() == 1, "Unexpected user population"
        admin = Users.query.filter_by(name="admin", type="admin").one()
        counts = {"users": Users.query.count(), "teams": Teams.query.count(),
                  "challenges": Challenges.query.count(), "solves": Solves.query.count(),
                  "rooms": QuestRoom.query.count(), "event_status": QuestEvent.query.one().status}
        assert counts == {"users": 1, "teams": 0, "challenges": 0, "solves": 0, "rooms": 5, "event_status": "draft"}, "Fresh install or restart changed event data"
    base_url = "https://localhost"
    with app.test_client() as client:
        response = client.get("/login", base_url=base_url)
        assert response.status_code == 200, "Native login page failed"
        with client.session_transaction(base_url=base_url) as session:
            nonce = session["nonce"]
        response = client.post("/login", base_url=base_url,
                               data={"name": "admin", "password": password, "nonce": nonce})
        assert response.status_code == 302, "Native administrator login failed"
        assert any("; Secure" in cookie for cookie in response.headers.getlist("Set-Cookie")), "Login did not set a secure cookie"
        for route in ("/api/v1/quest/bootstrap", "/api/v1/quest/sync"):
            response = client.get(route, base_url=base_url)
            assert response.status_code == 200, "Authenticated Quest endpoint failed"
            envelope = response.get_json()
            assert envelope["success"] is True and envelope["data"]["viewer"]["role"] == "administrator", "Native session was not accepted by Quest"
            assert envelope["data"]["event"]["status"] == "draft", "Event did not remain in draft"
        assert client.get("/admin", base_url=base_url, follow_redirects=True).status_code == 200, "Native admin endpoint failed"
    with app.app_context():
        fingerprint = hashlib.sha256(Users.query.filter_by(name="admin", type="admin").one().password.encode()).hexdigest()
print("QUEST_DEPLOY_SMOKE_JSON:" + json.dumps({"admin_password_fingerprint": fingerprint, "counts": counts, "secure_cookie": True, "native_admin_login": True, "bootstrap_sync": True}))
'''.replace("PASSWORD_LITERAL", repr(password))


def smoke(state_path):
    if platform.system() != "Linux" or platform.machine().lower() not in ("x86_64", "amd64"):
        raise SmokeError("Deployment smoke requires a Linux amd64 Docker runner")
    import yaml
    state_path = state_path.resolve()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    if state_path.exists():
        raise SmokeError("A previous smoke state exists; clean that run before starting another")
    directory = Path(tempfile.mkdtemp(prefix="quest-deploy-smoke-", dir=state_path.parent))
    state = {"directory": str(directory), "cluster": "quest-deploy-" + uuid.uuid4().hex[:12],
             "kubeconfig": str(directory / "kubeconfig"), "creation_attempted": False}
    save_state(state_path, state)
    environment = environment_for(state)
    kind, kubectl = directory / "bin" / "kind", directory / "bin" / "kubectl"

    def kubernetes(arguments, label, *, body=None, timeout=300):
        request_timeout = timeout if "rollout" in arguments or "exec" in arguments else min(timeout, 30)
        return run([str(kubectl), "--kubeconfig", state["kubeconfig"],
                    "--context", "kind-" + state["cluster"], "--request-timeout=" + str(request_timeout) + "s", *arguments],
                   label, environment=environment, body=body, timeout=timeout)

    def inspect():
        output = kubernetes(["-n", "ctfd", "exec", "-i", "deployment/ctfd", "-c", "ctfd", "--", "python", "-"],
                            "Native login and event persistence verification", body=probe_script(password), timeout=120)
        matches = [line[len(RESULT_PREFIX):] for line in output.splitlines() if line.startswith(RESULT_PREFIX)]
        if len(matches) != 1:
            raise SmokeError("Runtime did not return one safe verification summary")
        return json.loads(matches[0])

    def service_login():
        login = kubernetes(["get", "--raw", "/api/v1/namespaces/ctfd/services/ctfd:http/proxy/login"],
                           "CTFd Service login access")
        if "<html" not in login.lower():
            raise SmokeError("CTFd Service did not return the native login page")

    def pvc_uids():
        items = json.loads(kubernetes(["-n", "ctfd", "get", "pvc", "-o", "json"], "PVC persistence verification"))["items"]
        if {item["metadata"]["name"] for item in items} != {"ctfd-data", "database-data", "cache-data"}:
            raise SmokeError("Deployment did not create exactly the expected three PVCs")
        if any(item["status"]["phase"] != "Bound" for item in items):
            raise SmokeError("Deployment storage did not bind")
        return {item["metadata"]["name"]: item["metadata"]["uid"] for item in items}

    try:
        download_binary("https://kind.sigs.k8s.io/dl/" + KIND_VERSION + "/kind-linux-amd64", KIND_SHA256, kind)
        download_binary("https://dl.k8s.io/release/" + KUBECTL_VERSION + "/bin/linux/amd64/kubectl", KUBECTL_SHA256, kubectl)
        print("Verified kind and kubectl downloads", flush=True)
        run(["docker", "build", "--target", "runtime", "--build-arg", "PLUGIN_VERSION=dev", "--tag", LOCAL_IMAGE, str(ROOT)],
            "Runtime image build", environment=environment, timeout=900)
        state["creation_attempted"] = True
        save_state(state_path, state)
        run([str(kind), "create", "cluster", "--name", state["cluster"], "--image", NODE_IMAGE,
             "--kubeconfig", state["kubeconfig"], "--wait", "180s"],
            "Isolated kind cluster creation", environment=environment, timeout=300)
        Path(state["kubeconfig"]).chmod(0o600)
        run([str(kind), "load", "docker-image", LOCAL_IMAGE, "--name", state["cluster"]],
            "Local runtime image import", environment=environment, timeout=180)
        print("Isolated Kubernetes cluster is ready", flush=True)

        deployment = directory / "deploy"
        shutil.copytree(ROOT / "deploy", deployment)
        customization = deployment / "base" / "kustomization.yaml"
        settings = yaml.safe_load(customization.read_text())
        entries = [image for image in settings["images"] if image["name"] == "ghcr.io/open-tech-quest/ctfd-plugin"]
        if len(entries) != 1:
            raise SmokeError("Cannot locate the deployment's CTFd image override")
        entries[0].update(newName="quest-deploy", newTag="test")
        entries[0].pop("digest", None)
        customization.write_text(yaml.safe_dump(settings, sort_keys=False))
        private = directory / "ctfd.env"
        run([sys.executable, str(ROOT / "scripts" / "create-deploy-env.py"), "--output", str(private)],
            "Private test credential creation", environment=environment)
        if private.stat().st_mode & 0o777 != 0o600:
            raise SmokeError("Test credentials were not created with private permissions")
        values = dict(line.split("=", 1) for line in private.read_text().splitlines() if line)
        password = values["CTFD_ADMIN_PASSWORD"]
        kubernetes(["create", "namespace", "ctfd"], "Test namespace creation")
        kubernetes(["-n", "ctfd", "create", "secret", "generic", "ctfd-secrets", "--from-env-file=" + str(private)],
                   "Private test Secret creation")
        rendered = run(["kustomize", "build", str(deployment / "overlays" / "kubernetes")],
                       "Kubernetes overlay rendering", environment=environment)
        kubernetes(["apply", "-f", "-"], "Kubernetes overlay installation", body=rendered)
        for name in ("database", "cache", "ctfd"):
            kubernetes(["-n", "ctfd", "rollout", "status", "deployment/" + name, "--timeout=300s"],
                       name + " readiness", timeout=330)
        service_login()
        first, original_pvcs = inspect(), pvc_uids()
        print("Fresh installation and native administrator login passed", flush=True)
        kubernetes(["-n", "ctfd", "rollout", "restart", "deployment/ctfd"], "CTFd restart")
        kubernetes(["-n", "ctfd", "rollout", "status", "deployment/ctfd", "--timeout=300s"],
                   "Restarted CTFd readiness", timeout=330)
        service_login()
        second, restarted_pvcs = inspect(), pvc_uids()
        if first != second or original_pvcs != restarted_pvcs:
            raise SmokeError("CTFd restart changed credentials, event data or persistent volume identity")
        result = {"passed": True, "platform": "Linux amd64 kind", "kubernetes": KUBECTL_VERSION,
                  "checks": {"service_login": True, "native_admin_login": True, "bootstrap_sync": True,
                             "secure_cookie": True, "restart_preserved_credentials_and_data": True,
                             "pvc_uids_preserved": True}, "counts": second["counts"],
                  "admin_password_fingerprint": second["admin_password_fingerprint"],
                  "pvc_uids": restarted_pvcs,
                  "not_exercised": ["Ingress routing and TLS termination", "NetworkPolicy enforcement", "OpenShift SCC and router admission"]}
    except Exception:
        # Status reasons help identify startup failures without exposing pod
        # logs, container arguments, environment values or exception bodies.
        try:
            pods = json.loads(kubernetes(["-n", "ctfd", "get", "pods", "-o", "json"],
                                         "Safe startup diagnostics", timeout=15))["items"]
            statuses = []
            for pod in pods:
                containers = pod.get("status", {}).get("initContainerStatuses", []) + pod.get("status", {}).get("containerStatuses", [])
                statuses.append({"pod": pod["metadata"]["name"], "phase": pod.get("status", {}).get("phase"),
                                 "containers": [{"name": item["name"], "ready": item.get("ready", False),
                                                 "reasons": [state.get("reason") for state in item.get("state", {}).values() if state.get("reason")]}
                                                for item in containers]})
            print(json.dumps({"startup_status": statuses}, sort_keys=True), flush=True)
        except Exception:
            pass
        raise
    finally:
        cleanup(state_path)
    result["checks"]["isolated_cluster_removed"] = True
    print(json.dumps(result, sort_keys=True), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, help="Private state file under runner temp, used for cleanup retries")
    parser.add_argument("--cleanup", type=Path, help="Clean only the isolated run recorded in this state file")
    arguments = parser.parse_args()
    if bool(arguments.state) == bool(arguments.cleanup):
        parser.error("Choose either --state or --cleanup")
    try:
        if arguments.cleanup:
            cleanup(arguments.cleanup.resolve())
        else:
            def interrupted(signum, frame):
                raise SmokeError("Deployment smoke interrupted")
            signal.signal(signal.SIGTERM, interrupted)
            signal.signal(signal.SIGINT, interrupted)
            smoke(arguments.state)
    except (SmokeError, OSError, ValueError, KeyError) as error:
        # Exception text from subprocess output or HTTP bodies is never emitted.
        if isinstance(error, SmokeError):
            parser.exit(1, str(error) + "\n")
        parser.exit(1, "Deployment smoke could not complete its isolated setup or verification\n")


if __name__ == "__main__":
    main()
