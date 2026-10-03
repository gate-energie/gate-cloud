"""The Helm chart ships the dashboard template directory and mounts it where the sync reads it."""
import shutil
import subprocess
from pathlib import Path

import kubernetes
import pytest
import yaml
from dagster_k8s.models import k8s_model_from_dict, k8s_snake_case_dict

from gate_cloud.dashboard import referenced_files

CHART = Path(__file__).resolve().parents[1] / "charts" / "gate-cloud"
TEMPLATE_DIR = CHART / "files" / "dashboard"


def shipped_files() -> list[str]:
    """What the chart puts in the gate-cloud-dashboard ConfigMap (tests/ stays out)."""
    widgets = sorted(p.relative_to(TEMPLATE_DIR).as_posix() for p in (TEMPLATE_DIR / "widgets").glob("*.js"))
    return ["dashboard.json", "theme.css", *widgets]


def flat(path: str) -> str:
    return path.replace("/", "__")


def user_deployment_values() -> dict:
    values = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))
    return values["dagster"]["dagster-user-deployments"]["deployments"][0]


def dashboard_items(volumes: list[dict]) -> list[dict]:
    for volume in volumes:
        for source in volume.get("projected", {}).get("sources", []):
            if source.get("configMap", {}).get("name") == "gate-cloud-dashboard":
                return source["configMap"]["items"]
    raise AssertionError("no volume projects the gate-cloud-dashboard ConfigMap")


def test_shipped_files_cover_every_referenced_file():
    assert set(referenced_files(TEMPLATE_DIR)) <= set(shipped_files())


def test_values_mount_every_shipped_file_at_its_path():
    deployment = user_deployment_values()
    items = dashboard_items(deployment["volumes"])
    assert sorted((i["key"], i["path"]) for i in items) == sorted(
        (flat(f), f"dashboard/{f}") for f in shipped_files())
    assert {"name": deployment["volumes"][0]["name"], "mountPath": "/etc/gate-cloud", "readOnly": True} \
        in deployment["volumeMounts"]
    assert deployment["includeConfigInLaunchedRuns"]["enabled"] is True


def test_run_pods_accept_the_volume():
    # includeConfigInLaunchedRuns hands the volumes to the K8sRunLauncher, which parses them like this.
    for volume in user_deployment_values()["volumes"]:
        model = k8s_model_from_dict(kubernetes.client.V1Volume, k8s_snake_case_dict(kubernetes.client.V1Volume, volume))
        assert model.projected.sources[1].config_map.items[0].path == "dashboard/dashboard.json"


@pytest.mark.skipif(shutil.which("helm") is None or not (CHART / "charts").is_dir(),
                    reason="needs helm and `helm dependency build charts/gate-cloud`")
def test_helm_renders_the_dashboard_configmap_and_mount():
    rendered = subprocess.run(
        ["helm", "template", "ci", str(CHART), "--set", "monitorDeviceId=ci",
         "--set", "dagster.postgresql.postgresqlPassword=ci"],
        check=True, capture_output=True, text=True).stdout
    docs = [doc for doc in yaml.safe_load_all(rendered) if doc]
    by_name = {(doc["kind"], doc["metadata"]["name"]): doc for doc in docs}

    data = by_name[("ConfigMap", "gate-cloud-dashboard")]["data"]
    assert sorted(data) == sorted(flat(f) for f in shipped_files())
    for f in shipped_files():
        assert data[flat(f)] == (TEMPLATE_DIR / f).read_text(encoding="utf-8"), f

    assert by_name[("ConfigMap", "gate-cloud-env")]["data"]["GATE_DASHBOARD_PATH"] == "/etc/gate-cloud/dashboard"
    assert set(by_name[("ConfigMap", "gate-cloud-asset-model")]["data"]) == {"asset_model.yaml"}

    pod = by_name[("Deployment", "ci-dagster-user-deployments-gate-cloud")]["spec"]["template"]["spec"]
    assert len(dashboard_items(pod["volumes"])) == len(shipped_files())
