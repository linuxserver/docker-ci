import os
from unittest.mock import Mock
import json

import pytest
from docker.models.containers import Container
import chromedriver_autoinstaller
from docker import DockerClient
from moto import mock_aws

from ci.ci import CI, SetEnvs

os.environ["DRY_RUN"] = "false"
os.environ["IMAGE"] = "linuxserver/test"
os.environ["BASE"] = "alpine"
os.environ["ACCESS_KEY"] = "secret-access-key"
os.environ["SECRET_KEY"] = "secret-key"
os.environ["META_TAG"] = "test-meta-tag"
os.environ["TAGS"] = "amd64-nightly-5.10.1.9109-ls85|arm64v8-nightly-5.10.1.9109-ls85"
os.environ["CI_LOG_LEVEL"] = "ERROR"
os.environ["NODE_NAME"] = "test-node"
os.environ["SSL"] = "true"
os.environ["PORT"] = "443"
os.environ["WEB_SCREENSHOT"] = "true"
os.environ["WEB_AUTH"] = ""

@pytest.fixture
def sbom_blob() -> bytes:
    with open("tests/sbom_blob.txt", "rb") as f:
        yield f.read()

@pytest.fixture
def syft_mock_container(sbom_blob:bytes) -> Mock:
    container = Mock(spec=Container)
    container.logs = Mock(return_value=sbom_blob)
    container.reload = Mock(return_value=None)
    container.remove = Mock(return_value=None)
    yield container

@pytest.fixture
def ci(tmpdir, syft_mock_container: Mock) -> CI:
    ci = CI()
    ci.client = Mock(DockerClient)
    ci.client.containers = Mock()
    ci.client.containers.run = Mock(return_value=syft_mock_container)
    ci.outdir = tmpdir
    yield ci

@pytest.fixture
def set_envs() -> SetEnvs:
    set_envs = SetEnvs()
    yield set_envs

@pytest.fixture
def mock_attrs() -> dict:
    with open("tests/mock_attrs.json", encoding="utf-8") as f:
        yield json.load(f)

@pytest.fixture
def mock_image_attrs() -> dict:
    with open("tests/mock_image_attrs.json", encoding="utf-8") as f:
        yield json.load(f)

@pytest.fixture
def log_blob() -> bytes:
    with open("tests/log_blob.log", "rb") as f:
        yield f.read()

@pytest.fixture
def report_containers() -> dict:
    with open("tests/report.json", encoding="utf-8") as f:
        yield json.load(f)

@pytest.fixture
def mock_container(mock_attrs, mock_image_attrs, log_blob) -> Mock:
    container = Mock(spec=Container)
    container.attrs = mock_attrs
    container.image.attrs = mock_image_attrs
    container.logs = Mock(return_value=log_blob)
    container.reload = Mock(return_value=None)
    container.remove = Mock(return_value=None)
    yield container

@pytest.fixture
def chromedriver_path(tmpdir):
    path: None | str = chromedriver_autoinstaller.install(path=tmpdir)
    yield path

def test_convert_env(set_envs: SetEnvs):
    envs = "ENV1=VALUE1|ENV2=VALUE2"
    assert set_envs._split_key_value_string(envs) == {"ENV1": "VALUE1", "ENV2": "VALUE2"}
    assert set_envs._split_key_value_string(envs, make_list=True) == ["ENV1:VALUE1", "ENV2:VALUE2"]
    envs = "ENV1=VALUE1"
    assert set_envs._split_key_value_string(envs) == {"ENV1": "VALUE1"}
    assert set_envs._split_key_value_string(envs, make_list=True) == ["ENV1:VALUE1"]
    envs = "ENV1=VALUE1|"
    assert set_envs._split_key_value_string(envs) == {"ENV1": "VALUE1"}
    assert set_envs._split_key_value_string(envs, make_list=True) == ["ENV1:VALUE1"]
    envs = "ENV1=VALUE1|ENV2"
    assert set_envs._split_key_value_string(envs) == {"ENV1": "VALUE1"}
    assert set_envs._split_key_value_string(envs, make_list=True) == ["ENV1:VALUE1"]
    envs = "ENV1="
    assert set_envs._split_key_value_string(envs) == {}
    assert set_envs._split_key_value_string(envs, make_list=True) == []
    envs = "ENV1=|ENV2|"
    assert set_envs._split_key_value_string(envs) == {}
    assert set_envs._split_key_value_string(envs, make_list=True) == []

def test_add_test_result(ci: CI):
    for tag in ci.tags:
        ci._add_test_result(tag=tag, test=f"test-{tag}", status="PASS", message="-", start_time="")
        assert ci.tag_report_tests[tag] == {'test': {f"test-{tag}": {"status": "PASS", "message": "-", "runtime": "-"}}}

def test_get_build_info(ci: CI, mock_container: Mock):
    info: dict[str, str] = ci.get_build_info(mock_container,ci.tags[0])
    mock_info: dict[str, str] = {
                "version": "2.4.3.4248-ls7",
                "created": "2024-08-21T02:17:44+00:00",
                "size": '275.93MB',
                "maintainer": "Roxedus,thespad",
                "builder": "test-node"
            }
    assert info == mock_info

def test_get_platform(ci: CI):
    assert ci.get_platform(ci.tags[0]) == "amd64"
    assert ci.get_platform(ci.tags[1]) == "arm64"

def test_watch_container_logs(ci: CI, mock_container: Mock):
    ci.watch_container_logs(mock_container, ci.tags[0])
    assert ci.tag_report_tests[ci.tags[0]]["test"]["Container startup"]["status"] == "PASS"

def test_take_screenshot(ci:CI,mock_container: Mock):
    screenshot: bool = ci.take_screenshot(mock_container, ci.tags[0])
    if screenshot:
        assert os.path.isfile(os.path.join(ci.outdir, f"{ci.tags[0]}.png")) is True
        assert ci.tag_report_tests[ci.tags[0]]["test"]["Get screenshot"]["status"] == "PASS"
    else:
        assert ci.tag_report_tests[ci.tags[0]]["test"]["Get screenshot"]["status"] == "FAIL"

def test_create_html_ansi_file(ci:CI, log_blob:bytes):
    logs = log_blob.decode("utf-8")
    ci.create_html_ansi_file(logs,ci.tags[0],"log")
    assert os.path.isfile(os.path.join(ci.outdir,f"{ci.tags[0]}.log.html")) is True

def test_report_render(ci:CI, report_containers:dict):
    ci.report_containers = report_containers
    ci.report_render()
    assert os.path.isfile(os.path.join(ci.outdir,"index.html")) is True

def test_json_render(ci:CI, report_containers:dict):
    ci.report_containers = report_containers
    ci.json_render()
    assert os.path.isfile(os.path.join(ci.outdir,"report.json")) is True

def test_badge_render(ci:CI):
    ci.badge_render()
    assert os.path.isfile(os.path.join(ci.outdir,"ci-status.yml")) is True

def test_generate_sbom(ci:CI, syft_mock_container:Mock, sbom_blob:bytes):
    sbom = ci.generate_sbom(ci.tags[0])
    assert "VERSION" in sbom

def test_create_s3_client(ci:CI):
    with mock_aws():
        ci.s3_client = ci.create_s3_client()
        assert ci.s3_client is not None

def test_upload_file(ci: CI) -> None:
    with mock_aws():
        # Create the mock S3 client
        ci.s3_client = ci.create_s3_client()
        # Create the bucket
        ci.s3_client.create_bucket(Bucket=ci.bucket)
        # Upload a file to the bucket
        ci.upload_file("tests/log_blob.log", "log_blob.log", {"ContentType": "text/plain", "ACL": "public-read"})
