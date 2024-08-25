import os
from unittest.mock import Mock
import json

import pytest
from docker.models.containers import Container

from ci.ci import CI, SetEnvs

os.environ["DRY_RUN"] = "true"
os.environ["IMAGE"] = "linuxserver/test"
os.environ["BASE"] = "alpine"
os.environ["ACCESS_KEY"] = "secret-access-key"
os.environ["SECRET_KEY"] = "secret-key"
os.environ["META_TAG"] = "test-meta-tag"
os.environ["TAGS"] = "amd64|arm64"
os.environ["CI_LOG_LEVEL"] = "DEBUG"
os.environ["NODE_NAME"] = "test-node"

@pytest.fixture
def ci(tmpdir) -> CI:
    ci = CI()
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
    yield container

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

def test_mock_container(ci:CI,mock_container: Mock):
    info = ci.get_build_info(mock_container,"amd64")
    mock_info = {
                "version": "2.4.3.4248-ls7",
                "created": "2024-08-21T02:17:44+00:00",
                "size": '275.93MB',
                "maintainer": "Roxedus,thespad",
                "builder": "test-node"
            }
    assert info == mock_info
    
    ci.watch_container_logs(mock_container, "amd64")
    assert ci.tag_report_tests["amd64"]["test"]["Container startup"]["status"] == "PASS"


def test_create_html_ansi_file(ci:CI, log_blob:bytes):
    logs = log_blob.decode("utf-8")
    ci.create_html_ansi_file(logs,"amd64","log")
    assert os.path.isfile(os.path.join(ci.outdir,"amd64.log.html")) is True
    
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
