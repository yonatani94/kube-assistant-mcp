import yaml

from kube_assistant_mcp.manifest_gen import DeploymentSpec, ManifestGenerator


def test_deployment_manifest_has_expected_fields():
    gen = ManifestGenerator()
    spec = DeploymentSpec(name="myapp", namespace="prod", image="myrepo/myapp:1.2.3", container_port=9000)
    manifest = gen.deployment_manifest(spec)

    assert manifest["kind"] == "Deployment"
    assert manifest["metadata"]["name"] == "myapp"
    assert manifest["metadata"]["namespace"] == "prod"
    container = manifest["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == "myrepo/myapp:1.2.3"
    assert container["ports"][0]["containerPort"] == 9000


def test_service_manifest_targets_container_port():
    gen = ManifestGenerator()
    spec = DeploymentSpec(name="myapp", container_port=9000)
    service = gen.service_manifest(spec, service_port=80)

    assert service["kind"] == "Service"
    assert service["spec"]["ports"][0]["targetPort"] == 9000
    assert service["spec"]["ports"][0]["port"] == 80


def test_to_yaml_produces_valid_multidoc_yaml():
    gen = ManifestGenerator()
    spec = DeploymentSpec(name="myapp")
    deployment = gen.deployment_manifest(spec)
    service = gen.service_manifest(spec)

    yaml_text = gen.to_yaml(deployment, service)
    docs = list(yaml.safe_load_all(yaml_text))

    assert len(docs) == 2
    assert docs[0]["kind"] == "Deployment"
    assert docs[1]["kind"] == "Service"


def test_write_helm_chart_creates_expected_files(tmp_path):
    gen = ManifestGenerator()
    spec = DeploymentSpec(name="myapp", image="myrepo/myapp:1.2.3")

    chart_dir = gen.write_helm_chart(spec, tmp_path)

    assert (chart_dir / "Chart.yaml").exists()
    assert (chart_dir / "values.yaml").exists()
    assert (chart_dir / "templates" / "deployment.yaml").exists()
    assert (chart_dir / "templates" / "service.yaml").exists()

    values = yaml.safe_load((chart_dir / "values.yaml").read_text())
    assert values["image"]["repository"] == "myrepo/myapp"
    assert values["image"]["tag"] == "1.2.3"
