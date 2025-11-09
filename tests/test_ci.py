import os
from unittest.mock import Mock, patch
import json

import pytest
from docker.models.containers import Container
import chromedriver_autoinstaller
from docker import DockerClient
from moto import mock_aws

from ci.ci import CI, SetEnvs, CITestResult, CITests, Platform, BuildCacheTag

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
os.environ["COMMIT_SHA"] = "test-commit-sha"
os.environ["BUILD_NUMBER"] = "1234"

@pytest.fixture
def sbom_blob() -> bytes:
    with open("tests/sbom_blob.txt", "rb") as f:
        yield f.read()

@pytest.fixture
def sbom_buildx_blob() -> str:
    with open("tests/sbom_buildx_blob.txt", "rb") as f:
        data = f.read()
    yield data.decode("utf-8").strip()

@pytest.fixture
def sbom_buildx_formatted_blob() -> str:
    with open("tests/sbom_buildx_formatted_blob.txt", "rb") as f:
        data = f.read()
    yield data.decode("utf-8").strip()

@pytest.fixture
def syft_mock_container(sbom_blob:bytes) -> Mock:
    container = Mock(spec=Container)
    container.logs = Mock(return_value=sbom_blob)
    container.reload = Mock(return_value=None)
    container.remove = Mock(return_value=None)
    yield container

@pytest.fixture
def ci(tmpdir, syft_mock_container: Mock, sbom_buildx_blob: str) -> CI:
    ci = CI()
    ci.client = Mock(DockerClient)
    ci.client.containers = Mock()
    ci.client.containers.run = Mock(return_value=syft_mock_container)
    ci.get_sbom_buildx_blob = Mock(return_value=sbom_buildx_blob)
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
        ci._add_test_result(tag=tag, test=CITests.CONTAINER_START, status=CITestResult.PASS, message="-", start_time="")
        assert ci.tag_report_tests[tag] == {'test': {CITests.CONTAINER_START.value: {"status": "PASS", "message": "-", "runtime": "-"}}}

def test_get_build_info(ci: CI, mock_container: Mock):
    info: dict[str, str] = ci.get_build_info(mock_container,ci.tags[0])
    mock_info: dict[str, str] = {
                "version": "2.4.3.4248-ls7",
                "created": "2024-08-21T02:17:44+00:00",
                "size": '275.93MB',
                "maintainer": "Roxedus,thespad",
                "builder": "test-node",
                "tag": "amd64-nightly-5.10.1.9109-ls85",
                "image": "linuxserver/test",
            }
    assert info == mock_info

def test_get_platform(ci: CI):
    assert ci.get_platform(ci.tags[0]) == Platform.AMD64.value
    assert ci.get_platform(ci.tags[1]) == Platform.ARM64.value

def test_watch_container_logs(ci: CI, mock_container: Mock):
    ci.watch_container_logs(mock_container, ci.tags[0])
    assert ci.tag_report_tests[ci.tags[0]]["test"][CITests.CONTAINER_START.value]["status"] == CITestResult.PASS.value

def test_take_screenshot(ci:CI,mock_container: Mock):
    screenshot: bool = ci.take_screenshot(mock_container, ci.tags[0])
    if screenshot:
        assert os.path.isfile(os.path.join(ci.outdir, f"{ci.tags[0]}.png")) is True
        assert ci.tag_report_tests[ci.tags[0]]["test"][CITests.CAPTURE_SCREENSHOT.value]["status"] == CITestResult.PASS.value
    else:
        assert ci.tag_report_tests[ci.tags[0]]["test"][CITests.CAPTURE_SCREENSHOT.value]["status"] == CITestResult.FAIL.value

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

def test_get_sbom_syft(ci:CI, syft_mock_container:Mock, sbom_blob:bytes):
    sbom = ci.get_sbom_syft(ci.tags[0])
    assert "VERSION" in sbom

def test_parse_buildx_sbom(ci:CI, sbom_buildx_blob:str):
    packages = ci.parse_buildx_sbom(sbom_buildx_blob)
    assert len(packages) == 148
    assert packages[0]["name"] == "adduser"
    assert packages[0]["version"] == "3.137ubuntu1"

def test_format_package_table(ci:CI, sbom_buildx_blob:str, sbom_buildx_formatted_blob: str):
    packages = ci.parse_buildx_sbom(sbom_buildx_blob)
    table = ci.format_package_table(packages)
    assert "VERSION" in table
    assert "cron" in table
    assert "3.0pl1-184ubuntu2" in table

def test_get_sbom_buildx_blob(ci: CI, sbom_buildx_blob: str) -> None:
    expected_output = sbom_buildx_blob
    mock_completed_process = Mock()
    mock_completed_process.returncode = 0
    mock_completed_process.stdout = expected_output

    with patch("subprocess.run", return_value=mock_completed_process):
        sbom = ci.get_sbom_buildx_blob(ci.tags[0])
        assert sbom.strip() == expected_output.strip()

def test_make_sbom(ci: CI, sbom_buildx_blob: str, sbom_buildx_formatted_blob: str) -> None:
    with patch.object(ci, 'get_sbom_buildx_blob', return_value=sbom_buildx_blob):
        packages = ci.make_sbom(ci.tags[0])
        assert os.path.isfile(os.path.join(ci.outdir,"amd64-nightly-5.10.1.9109-ls85.sbom.html")) is True
        assert "VERSION" in packages
        assert "cron" in packages
        assert "3.0pl1-184ubuntu2" in packages

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

def test_get_build_url(ci: CI) -> None:
    ci.image = "linuxserver/plex"
    tag = "amd64-nightly-5.10.1.9109-ls85"
    assert ci.get_build_url(tag) == f"https://ghcr.io/{ci.image}:{tag}"
    ci.image = "lsiodev/plex"
    assert ci.get_build_url(tag) == f"https://ghcr.io/linuxserver/lsiodev-plex:{tag}"
    ci.image = "lspipepr/plex"
    assert ci.get_build_url(tag) == f"https://ghcr.io/linuxserver/lspipepr-plex:{tag}"
    ci.image = "lsiobase/ubuntu"
    assert ci.get_build_url(tag) == f"https://ghcr.io/linuxserver/baseimage-ubuntu:{tag}"

def test_get_image_name(ci: CI) -> None:
    ci.image = "linuxserver/plex"
    assert ci.get_image_name() == "linuxserver/plex"
    ci.image = "lsiodev/plex"
    assert ci.get_image_name() == "linuxserver/lsiodev-plex"
    ci.image = "lspipepr/plex"
    assert ci.get_image_name() == "linuxserver/lspipepr-plex"
    ci.image = "lsiobase/ubuntu"
    assert ci.get_image_name() == "linuxserver/docker-baseimage-ubuntu"

def test_get_build_cache_url(ci: CI) -> None:
    for tag in ci.tags:
        cache_tag = ci.get_build_cache_platform(tag)
        expected_url = f"{ci.build_cache_registry}:{cache_tag}-{ci.commit_sha}-{ci.build_number}"
        assert ci.get_build_cache_url(tag) == expected_url

def test_get_build_cache_platform(ci: CI) -> None:
    assert ci.get_build_cache_platform(ci.tags[0]) == BuildCacheTag.AMD64.value
    assert ci.get_build_cache_platform(ci.tags[1]) == BuildCacheTag.ARM64.value
