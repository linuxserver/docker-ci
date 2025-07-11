#!/usr/bin/env python3

from multiprocessing.pool import ThreadPool
from threading import current_thread
from concurrent.futures import Future, ThreadPoolExecutor
import os
import shutil
import time
import logging
from logging import Logger
import mimetypes
import json
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from functools import wraps
from typing import Callable, Any, Literal
from textwrap import dedent

import boto3
import requests
from boto3.exceptions import S3UploadFailedError
from botocore.exceptions import ClientError
import docker
from docker.errors import APIError,ContainerError,ImageNotFound
from docker.models.containers import Container, ExecResult
from docker import DockerClient
import anybadge
from ansi2html import Ansi2HTMLConverter
from selenium import webdriver
from selenium.webdriver.chrome.webdriver import WebDriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from jinja2 import Environment, FileSystemLoader, select_autoescape, Template
from pyvirtualdisplay import Display

logger: Logger = logging.getLogger(__name__)

def testing(func: Callable):
    """If the DRY_RUN env is set and this decorator is used on a function it will return None

    Args:
        func (function): A function
    """
    @wraps(func)
    def wrapper(*args, **kwargs) -> Any | None:
        if os.environ.get("DRY_RUN") == "true":
            logger.warning("Dry run enabled, skipping execution of %s", func.__name__)
            return
        return func(*args,**kwargs)
    return wrapper

def deprecated(reason: str):
    """Decorator to mark a function as deprecated. Will log a warning when the function is called.

    Args:
        reason (str): The reason it is deprecated.
    """
    def deprecated_decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            logger.warning("Function %s is deprecated. Reason: %s", func.__name__, reason)
            return func(*args,**kwargs)
        return wrapper
    return deprecated_decorator

class SetEnvs():
    """Simple helper class that sets up the ENVs"""
    def __init__(self) -> None:
        self.logger: Logger = logging.getLogger("SetEnvs")

        os.environ["S6_VERBOSITY"] = os.environ.get("CI_S6_VERBOSITY","2")
        # Set the optional parameters
        self.dockerenv: dict[str, str] = self.convert_env(os.environ.get("DOCKER_ENV", ""))
        # self.docker_volumes: list[str] = self.convert_volumes(os.environ.get("DOCKER_VOLUMES", "")) # For future use
        # self.docker_privileged: bool = os.environ.get("DOCKER_PRIVILEGED", "false").lower() == "true" # For future use
        self.webauth: str = os.environ.get("WEB_AUTH", "user:password")
        self.webpath: str = os.environ.get("WEB_PATH", "")
        self.screenshot: bool = os.environ.get("WEB_SCREENSHOT", "false").lower() == "true"

        # Make sure the numeric values are set even if they are set to empty strings in the environment
        self.screenshot_timeout: int = (os.environ.get("WEB_SCREENSHOT_TIMEOUT", "120") or "120")
        self.screenshot_delay: int = (os.environ.get("WEB_SCREENSHOT_DELAY", "10") or "10")
        self.logs_timeout: int = (os.environ.get("DOCKER_LOGS_TIMEOUT", "120") or "120")
        self.sbom_timeout: int = (os.environ.get("SBOM_TIMEOUT", "900") or "900")
        self.port: int = (os.environ.get("PORT", "80") or "80")
        self.builder: str = os.environ.get("NODE_NAME", "-")
        self.ssl: str = os.environ.get("SSL", "false")
        self.region: str = os.environ.get("S3_REGION", "us-east-1")
        self.bucket: str = os.environ.get("S3_BUCKET", "ci-tests.linuxserver.io")
        self.release_tag: str = os.environ.get("RELEASE_TAG", "latest")
        self.syft_image_tag: str = os.environ.get("SYFT_IMAGE_TAG", "v1.26.1")

        if os.environ.get("DELAY_START"):
            self.logger.warning("DELAY_START env is obsolete, and not in use anymore")
        if os.environ.get("DOCKER_VOLUMES"):
            self.logger.warning("DOCKER_VOLUMES env is not in use")
        if os.environ.get("DOCKER_PRIVILEGED"):
            self.logger.warning("DOCKER_PRIVILEGED env is not in use")

        if os.environ.get("CI_LOCAL_MODE", "false").lower() == "true":
            self.logger.warning("--- LOCAL MODE ACTIVE ---")
            self.logger.warning("S3 uploads will be skipped and dummy keys will be used.")
            os.environ["DRY_RUN"] = "true"
            # Set dummy ENVs to pass the check_env() validation
            os.environ.setdefault("ACCESS_KEY", "local")
            os.environ.setdefault("SECRET_KEY", "local")
            # Use the first tag as the meta tag for a sensible output folder name
            first_tag = os.environ.get("TAGS", "local").split("|")[0]
            os.environ.setdefault("META_TAG", first_tag)
            os.environ.setdefault("RELEASE_TAG", first_tag)

        self.check_env()
        self.validate_attrs()

        env_data = dedent(f"""
        ENVIRONMENT DATA:
        NODE_NAME:              '{os.environ.get("NODE_NAME")}'
        IMAGE:                  '{os.environ.get("IMAGE")}'
        BASE:                   '{os.environ.get("BASE")}'
        META_TAG:               '{os.environ.get("META_TAG")}'
        RELEASE_TAG:            '{os.environ.get("RELEASE_TAG")}'
        TAGS:                   '{os.environ.get("TAGS")}'
        S6_VERBOSITY:           '{os.environ.get("S6_VERBOSITY")}'
        CI_S6_VERBOSITY         '{os.environ.get("CI_S6_VERBOSITY")}'
        CI_LOG_LEVEL            '{os.environ.get("CI_LOG_LEVEL")}'
        DOCKER_ENV:             '{os.environ.get("DOCKER_ENV")}'
        DOCKER_VOLUMES:         '{os.environ.get("DOCKER_VOLUMES")}' (Not in use)
        DOCKER_PRIVILEGED:      '{os.environ.get("DOCKER_PRIVILEGED")}' (Not in use)
        WEB_AUTH:               '{os.environ.get("WEB_AUTH")}'
        WEB_PATH:               '{os.environ.get("WEB_PATH")}'
        WEB_SCREENSHOT:         '{os.environ.get("WEB_SCREENSHOT")}'
        WEB_SCREENSHOT_TIMEOUT: '{os.environ.get("WEB_SCREENSHOT_TIMEOUT")}'
        WEB_SCREENSHOT_DELAY:   '{os.environ.get("WEB_SCREENSHOT_DELAY")}'
        DOCKER_LOGS_TIMEOUT:    '{os.environ.get("DOCKER_LOGS_TIMEOUT")}'
        SBOM_TIMEOUT:           '{os.environ.get("SBOM_TIMEOUT")}'
        DELAY_START:            '{os.environ.get("DELAY_START")}' (Not in use)
        PORT:                   '{os.environ.get("PORT")}'
        SSL:                    '{os.environ.get("SSL")}'
        S3_REGION:              '{os.environ.get("S3_REGION")}'
        S3_BUCKET:              '{os.environ.get("S3_BUCKET")}'
        SYFT_IMAGE_TAG:         '{os.environ.get("SYFT_IMAGE_TAG")}'
        Docker Engine Version:  '{self.get_docker_engine_version()}'
        """)
        self.logger.info(env_data)

    def get_docker_engine_version(self) -> str:
        """Get the Docker Engine version

        Returns:
            str: The Docker Engine version
        """
        try:
            return docker.from_env().version().get("Version")
        except Exception:
            logger.error("Failed to get Docker Engine version!")
            return "Unknown"

    def validate_attrs(self) -> None:
        """Validate the numeric environment variables"""
        try:
            self.screenshot_timeout = int(self.screenshot_timeout)
            self.screenshot_delay = int(self.screenshot_delay)
            self.logs_timeout = int(self.logs_timeout)
            self.sbom_timeout = int(self.sbom_timeout)
            self.port = int(self.port)
        except (ValueError,TypeError) as error:
            self.logger.exception("Failed to convert numeric envs to int!")
            raise CIError("Failed to convert numeric envs to int!") from error

    def _split_key_value_string(self, kv:str, make_list:bool = False) -> dict[str,str] | list[str]:
        """Split a key value string into a dictionary or list.

        Args:
            kv (str): A string with key values separated by the pipe symbol. e.g `key1=val1|key2=val2`.
            make_list (bool, optional): If the return value should be a list of strings where the key and value is separated by :. Defaults to False.

        Returns:
            dict[str,str]: Returns a dictionary with our keys and values.
        """
        if make_list:
            return [f"{k}:{v}" for k,v in (item.split("=") for item in kv.split("|") if item and "=" in item and item.split('=')[1])]
        return dict((item.split('=') for item in kv.split('|') if item and "=" in item and item.split('=')[1]))

    def convert_env(self, envs:str = None) -> dict[str,str]:
        """Convert env DOCKER_ENV to dictionary

        Args:
            envs (str, optional): A string with key values separated by the pipe symbol. e.g `key1=val1|key2=val2`. Defaults to None.

        Raises:
            CIError: Raises a CIError Exception if it failes to parse the string

        Returns:
            dict[str,str]: Returns a dictionary with our keys and values.
        """
        env_dict: dict = {}
        if envs:
            self.logger.info("Converting envs '%s' to dictionary", envs)
            try:
                env_dict = self._split_key_value_string(envs)
                env_dict["S6_VERBOSITY"] = os.environ.get("S6_VERBOSITY")
            except Exception as error:
                self.logger.exception("Failed to convert DOCKER_ENV: %s to dictionary!", envs)
                raise CIError(f"Failed converting DOCKER_ENV: {envs} to dictionary") from error
        return env_dict

    def convert_volumes(self, volumes:str = None) -> list[str]:
        """Convert env DOCKER_VOLUMES to list

        Args:
            volumes (str, optional): A string with key values separated by the pipe symbol. e.g `key1=val1|key2=val2`. Defaults to None.

        Raises:
            CIError: Raises a CIError Exception if it fails to parse the string

        Returns:
            list[str]: Returns a list with our keys and values.
        """
        volume_list: list = []
        if volumes:
            self.logger.info("Converting volumes '%s' to list", volumes)
            try:
                volume_list = self._split_key_value_string(volumes, make_list=True)
            except Exception as error:
                self.logger.exception("Failed to convert DOCKER_VOLUME: %s to list!", volumes)
                raise CIError(f"Failed converting DOCKER_VOLUME: {volumes} to list") from error
        return volume_list

    def check_env(self) -> None:
        """Make sure all needed ENVs are set

        Raises:
            CIError: Raises a CIError exception if one of the environment values is not set.
        """
        try:
            self.image: str = os.environ["IMAGE"]
            self.base: str = os.environ["BASE"]
            self.s3_key: str = os.environ["ACCESS_KEY"]
            self.s3_secret: str = os.environ["SECRET_KEY"]
            self.meta_tag: str = os.environ["META_TAG"]
            self.tags_env: str = os.environ["TAGS"]
        except KeyError as error:
            self.logger.exception("Key is not set in ENV!")
            raise CIError(f"Key {error} is not set in ENV!") from error


class CI(SetEnvs):
    """CI object to use for testing image tags.

    Attributes:
        client (DockerClient): Docker client object
        tags (list): List of tags to test
        tag_report_tests (dict): Dictionary to hold the test results for each tag
        report_containers (dict): Dictionary to hold the report information for each tag
        report_status (str): The status of the report
        outdir (str): The output directory
        s3_client (boto3.client): S3 client object

    Args:
        SetEnvs (Object): Helper class that initializes and checks that all the necessary environment variables exists. Object is initialized upon init of CI.
    """
    def __init__(self) -> None:
        super().__init__()  # Init the SetEnvs object.
        self.logger = logging.getLogger("LSIO CI")
        self.start_time: float = 0.0
        self.total_runtime: float = 0.0
        logging.getLogger("botocore.auth").setLevel(logging.INFO)  # Don't log the S3 authentication steps.

        self.client: DockerClient = self.create_docker_client()
        self.tags = list(self.tags_env.split("|"))
        self.tag_report_tests:dict[str,dict[str,dict]] = {tag: {"test":{}} for tag in self.tags} # Adds all the tags as keys with an empty dict as value to the dict
        self.report_containers: dict[str,dict[str,dict]] = {}
        self.report_status = "PASS"
        self.outdir: str = f"{os.path.dirname(os.path.realpath(__file__))}/output/{self.image}/{self.meta_tag}"
        os.makedirs(self.outdir, exist_ok=True)
        self.s3_client = self.create_s3_client()

    def run(self,tags: list) -> None:
        """Will iterate over all the tags running container_test() on each tag, multithreaded.

        Args:
            `tags` (list): All the tags we will test on the image.

        """
        self.start_time = time.time()
        thread_pool = ThreadPool(processes=10)
        thread_pool.map(self.container_test,tags)
        display = Display(size=(1920, 1080)) # Setup an x virtual frame buffer (Xvfb) that Selenium can use during the tests.
        display.start()
        thread_pool.close()
        thread_pool.join()
        display.stop()
        self.total_runtime = time.time() - self.start_time

    def container_test(self, tag: str) -> None:
        """Main container test logic.

        Args:
            `tag` (str): The container tag

        1. Spins up the container tag
            Checks the container logs for either `[services.d] done.` or `[ls.io-init] done.`
        2. Export the build version from the Container object.
        3. Export the package info (SBOM) from the Container object.
        4. Take a screenshot for the report if the screenshot env is true.
        5. Add report information to report.json.
        """
        start_time = time.time()
        # Name the thread for easier debugging.
        thread_name: str = f"{self.get_platform(tag).upper()}Thread"
        current_thread().name = thread_name

        # Start the container
        self.logger.info("Starting test of: %s", tag)
        container: Container = self.client.containers.run(f"{self.image}:{tag}",
                                               shm_size="1G",
                                               detach=True,
                                               environment=self.dockerenv)
        container_config: list[str] = container.attrs["Config"]["Env"]
        self.logger.info("Container config of tag %s: %s",tag,container_config)

        # Run these tests in parallel so the runtime data is more accurate.
        with ThreadPoolExecutor(max_workers=2,thread_name_prefix=thread_name) as executor:
            future_sbom: Future[str] = executor.submit(self.generate_sbom, tag)
            future_logs: Future[bool] = executor.submit(self.watch_container_logs, container, tag)

        sbom: str = future_sbom.result(self.sbom_timeout + 5) # Set a thread timeout if the function for some reason hangs
        logsfound: bool = future_logs.result(self.logs_timeout + 5) # Set a thread timeout if the function for some reason hangs
        build_info: dict = self.get_build_info(container,tag) # Get the image build info

        if not logsfound:
            self.logger.error("Test of %s FAILED after %.2f seconds", tag, time.time() - start_time)
            self._endtest(container, tag, build_info, sbom, False, start_time)
            return

        if build_info["version"] == "ERROR":
            self.logger.error("Test of %s FAILED after %.2f seconds", tag, time.time() - start_time)
            self._endtest(container, tag, build_info, sbom, False, start_time)
            return

        if sbom == "ERROR":
            self.logger.error("Test of %s FAILED after %.2f seconds", tag, time.time() - start_time)
            self._endtest(container, tag, build_info, sbom, False, start_time)
            return

        # Screenshot the web interface and check connectivity
        screenshot_success, browser_logs = self.take_screenshot(container, tag)
        if not screenshot_success and self.get_platform(tag) == "amd64":
            self.logger.error("Test of %s FAILED after %.2f seconds", tag, time.time() - start_time)
            self._endtest(container, tag, build_info, sbom, False, start_time, browser_logs)
            return

        self._endtest(container, tag, build_info, sbom, True, start_time, browser_logs)
        self.logger.success("Test of %s PASSED after %.2f seconds", tag, time.time() - start_time)
        return

    def _endtest(self, container:Container, tag:str, build_info:dict[str,str], packages:str, test_success: bool, start_time:float|int = 0.0, browser_logs: str = "") -> None:
        """End the test with as much info as we have and append to the report.

        Args:
            `container` (Container): Container object
            `tag` (str): The container tag
            `build_info` (str): Information about the build (version, size etc)
            `packages` (str): SBOM dump from the container
            `test_success` (bool): If the testing of the container failed or not
            `start_time` (float, optional): The start time of the test. Defaults to 0.0. Used to calculate the runtime of the test.
            `browser_logs` (str, optional): The browser console logs.
        """
        if not start_time:
            runtime = "-"
        if isinstance(start_time,(float, int)):
            runtime = f"{time.time() - start_time:.2f}s"
        logblob: str = container.logs(timestamps=True).decode("utf-8")
        self.create_html_ansi_file(logblob, tag, "log") # Generate an html container log file based on the latest logs
        try:
            container.remove(force="true")
        except APIError:
            self.logger.exception("Failed to remove container %s",tag)
        warning_texts: dict[str, str] = {
            "dotnet": "May be a .NET app. Service might not start on ARM32 with QEMU",
            "uwsgi": "This image uses uWSGI and might not start on ARM/QEMU"
        }
        # Add the info to the report
        self.report_containers[tag] = {
            "logs": logblob,
            "sysinfo": packages,
            "browser_logs": browser_logs,
            "warnings": {
                "dotnet": warning_texts["dotnet"] if "icu-libs" in packages and "arm32" in tag else "",
                "uwsgi": warning_texts["uwsgi"] if "uwsgi" in packages and "arm" in tag else ""
            },
            "build_info": build_info,
            "test_results": self.tag_report_tests[tag]["test"],
            "test_success": test_success,
            "runtime": runtime,
            "build_url": self.get_build_url(tag),
            "platform": self.get_platform(tag).upper()
            }
        self.report_containers[tag]["has_warnings"] = any(warning[1] for warning in self.report_containers[tag]["warnings"].items())

    def _get_browser_logs(self, driver: WebDriver, tag: str) -> str:
        """Get browser console logs from the webdriver.

        Args:
            driver (WebDriver): The selenium webdriver instance.
            tag (str): The container tag.

        Returns:
            str: The browser logs as a JSON formatted string.
        """
        try:
            self.logger.info("Getting browser console logs for tag %s", tag)
            browser_logs_list = driver.get_log('browser')
            browser_logs_str = json.dumps(browser_logs_list, indent=4)
            self.create_html_ansi_file(browser_logs_str, tag, "browser")
            return browser_logs_str
        except Exception:
            self.logger.exception("Failed to get browser console logs for tag %s", tag)
            return '{"error": "Failed to retrieve browser logs"}'

    def get_platform(self, tag: str) -> str:
        """Check the 5 first characters of the tag and return the platform.

        If no match is found return amd64.

        Returns:
            str: The platform
        """
        platform: str = tag[:5]
        match platform:
            case "amd64":
                return "amd64"
            case "arm64":
                return "arm64"
            case "arm32":
                return "arm"
            case "riscv":
                return "riscv64"
            case _:
                return "amd64"

    @deprecated(reason="Use generate_sbom instead")
    def export_package_info(self, container:Container, tag:str) -> str:
        """Dump the package info into a string for the report

        Args:
            container (Container): The container we are testing
            tag (str): The tag we are testing

        Returns:
            str: Return the output of the dump command or "ERROR"
        """
        # Dump package information
        dump_commands: dict[str, str] = {
            "alpine": "apk info -v",
            "debian": "apt list",
            "ubuntu": "apt list",
            "fedora": "rpm -qa",
            "arch": "pacman -Q"
            }
        try:
            self.logger.info("Dumping package info for %s",tag)
            info: ExecResult = container.exec_run(dump_commands[self.base])
            packages: str = info[1].decode("utf-8")
            if info[0] != 0:
                raise CIError(f"Failed to dump packages. Output: {packages}")
            self.tag_report_tests[tag]["test"]["Dump package info"] = (dict(sorted({
                "status":"PASS",
                "message":"-"}.items())))
            self.logger.info("Dump package info %s: PASS", tag)
        except (APIError, IndexError,CIError) as error:
            packages = "ERROR"
            self.logger.exception("Dumping package info on %s: FAIL", tag)
            self.tag_report_tests[tag]["test"]["Dump package info"] = (dict(sorted({
                "Dump package info":"FAIL",
                "message":str(error)}.items())))
            self.report_status = "FAIL"
        return packages

    def generate_sbom(self, tag:str) -> str:
        """Generate the SBOM for the image tag.

        Creates the output file in `{self.outdir}/{tag}.sbom.html`

        Args:
            tag (str): The tag we are testing

        Returns:
            bool: Return the output if successful otherwise "ERROR".
        """
        start_time = time.time()
        platform: str = self.get_platform(tag)
        syft:Container = self.client.containers.run(image=f"ghcr.io/anchore/syft:{self.syft_image_tag}",command=f"{self.image}:{tag} --platform=linux/{platform}",
            detach=True, volumes={"/var/run/docker.sock": {"bind": "/var/run/docker.sock", "mode": "rw"}})
        self.logger.info("Creating SBOM package list on %s with syft version %s",tag,self.syft_image_tag)
        test = "Create SBOM"
        t_end: float = time.time() + self.sbom_timeout
        self.logger.info("Tailing the syft container logs for %s seconds looking the 'VERSION' message on tag: %s",self.sbom_timeout,tag)
        error_message = "Did not find the 'VERSION' keyword in the syft container logs"
        while time.time() < t_end:
            time.sleep(5)
            try:
                logblob: str = syft.logs().decode("utf-8")
                if "VERSION" in logblob:
                    self.logger.info("Get package versions for %s completed", tag)
                    self._add_test_result(tag, test, "PASS", "-", start_time)
                    self.logger.success("%s package list %s: PASSED after %.2f seconds", test, tag, time.time() - start_time)
                    self.create_html_ansi_file(str(logblob),tag,"sbom")
                    try:
                        syft.remove(force=True)
                    except Exception:
                        self.logger.exception("Failed to remove the syft container, %s",tag)
                    return logblob
            except (APIError,ContainerError,ImageNotFound) as error:
                error_message: APIError | ContainerError | ImageNotFound = error
                self.logger.exception("Creating SBOM package list on %s: FAIL", tag)
        self.logger.error("Failed to generate SBOM output on tag %s. SBOM output:\n%s",tag, logblob)
        self.report_status = "FAIL"
        self._add_test_result(tag, test, "FAIL", str(error_message), start_time)
        try:
            syft.remove(force=True)
        except Exception:
            self.logger.exception("Failed to remove the syft container, %s",tag)
        return "ERROR"

    @deprecated(reason="Use get_build_info instead")
    def get_build_version(self,container:Container,tag:str) -> str:
        """Fetch the build version from the container object attributes.

        Args:
            container (Container): The container we are testing
            tag (str): The current tag we are testing

        Returns:
            str: Returns the build version or "ERROR"
        """
        try:
            self.logger.info("Fetching build version on tag: %s",tag)
            build_version: str = container.attrs["Config"]["Labels"]["build_version"]
            self.tag_report_tests[tag]["test"]["Get build version"] = (dict(sorted({
                "status":"PASS",
                "message":"-"}.items())))
            self.logger.info("Get build version on tag '%s': PASS", tag)
        except (APIError,KeyError) as error:
            self.logger.exception("Get build version on tag '%s': FAIL", tag)
            build_version = "ERROR"
            if isinstance(error,KeyError):
                error: str = f"KeyError: {error}"
            self.tag_report_tests[tag]["test"]["Get build version"] = (dict(sorted({
                "status":"FAIL",
                "message":str(error)}.items())))
            self.report_status = "FAIL"
        return build_version

    def get_image_name(self) -> str:
        """Get the image name from the IMAGE env.

        Returns:
            str: The container name
        """
        _, container_name = self.image.split("/")
        match self.image:
            case _ if "lspipepr" in self.image:
                return f"linuxserver/lspipepr-{container_name}"
            case _ if "lsiodev" in self.image:
                return f"linuxserver/lsiodev-{container_name}"
            case _ if "lsiobase" in self.image:
                return f"linuxserver/docker-baseimage-{container_name}"
            case _:
                return self.image

    def get_build_url(self, tag) -> str:
        """Get the build url from the IMAGE env.

        Args:
            tag (str): The tag we are testing

        Returns:
            dict: Returns a dictionary with the build url and container name
        """
        _, container_name = self.image.split("/")
        match self.image:
            case _ if "lspipepr" in self.image:
                return f"https://ghcr.io/linuxserver/lspipepr-{container_name}:{tag}"
            case _ if "lsiodev" in self.image:
                return f"https://ghcr.io/linuxserver/lsiodev-{container_name}:{tag}"
            case _ if "lsiobase" in self.image:
                return f"https://ghcr.io/linuxserver/baseimage-{container_name}:{tag}"
            case _:
                return f"https://ghcr.io/{self.image}:{tag}"

    def get_build_info(self,container:Container,tag:str) -> dict[str,str]:
        """Get the build information from the container object.

        Args:
            container (Container): The container we are testing
            tag (str): The tag we are testing

        Returns:
            dict[str,str]: Returns a dictionary with the build information

            ```
            {
                "version": "1.0.0",
                "created": "xxxx-xx-xx",
                "size": "100MB",
                "maintainer": "user"
                "builder": "node",
                "tag": "latest",
                "image": "linuxserver/xxx"
            }
            ```
        """
        test = "Get build info"
        start_time = time.time()
        try:
            self.logger.info("Fetching build info on tag: %s",tag)
            build_info: dict[str,str] = {
                "version": container.attrs["Config"]["Labels"]["org.opencontainers.image.version"],
                "created": container.attrs["Config"]["Labels"]["org.opencontainers.image.created"],
                "size": "%.2f" % float(int(container.image.attrs["Size"])/1000000) + "MB",
                "maintainer": container.attrs["Config"]["Labels"]["maintainer"],
                "builder": self.builder,
                "tag": tag,
                "image": self.get_image_name()
            }
            self._add_test_result(tag, test, "PASS", "-", start_time)
            self.logger.success("Get build info on tag '%s': PASS", tag)
        except (APIError,KeyError) as error:
            self.logger.exception("Get build info on tag '%s': FAIL", tag)
            build_info = {"version": "ERROR", "created": "ERROR", "size": "ERROR", "maintainer": "ERROR"}
            if isinstance(error,KeyError):
                error: str = f"KeyError: {error}"
            self._add_test_result(tag, test, "FAIL", str(error), start_time)
            self.report_status = "FAIL"
        return build_info

    def watch_container_logs(self, container:Container, tag:str) -> bool:
        """Tail the container logs for n seconds and look for the init done message that tells us the container started up
        successfully.

        Args:
            container (Container): The container we are testing
            tag (str): The tag we are testing

        Returns:
            bool: Return True if the "done" message is found, otherwise False.
        """
        test = "Container startup"
        start_time = time.time()
        t_end: float = time.time() + self.logs_timeout
        self.logger.info("Tailing the %s logs for %s seconds looking for the 'done' message", tag, self.logs_timeout)
        while time.time() < t_end:
            try:
                logblob: str = container.logs().decode("utf-8")
                if "[services.d] done." in logblob or "[ls.io-init] done." in logblob:
                    self.logger.info("%s completed for %s",test, tag)
                    self._add_test_result(tag, test, "PASS", "-", start_time)
                    self.logger.success("%s %s: PASSED after %.2f seconds", test, tag, time.time() - start_time)
                    return True
                time.sleep(1)
            except APIError as error:
                self.logger.exception("%s %s: FAIL - INIT NOT FINISHED", test, tag)
                self._add_test_result(tag, test, "FAIL", f"INIT NOT FINISHED: {str(error)}", start_time)
                self.report_status = "FAIL"
                return False
        self.logger.error("%s failed for %s", test, tag)
        self._add_test_result(tag, test, "FAIL", "INIT NOT FINISHED", start_time)
        self.logger.error("%s %s: FAIL - INIT NOT FINISHED", test, tag)
        self.report_status = "FAIL"
        return False

    def report_render(self) -> None:
        """Render the index file for upload"""
        self.logger.info("Rendering Report")
        env = Environment(autoescape=select_autoescape(enabled_extensions=("html", "xml"),default_for_string=True),
                          loader = FileSystemLoader(os.path.dirname(os.path.realpath(__file__))) )
        template: Template = env.get_template("template.html")
        self.report_containers = json.loads(json.dumps(self.report_containers,sort_keys=True))
        with open(f"{self.outdir}/index.html", mode="w", encoding="utf-8") as file_:
            file_.write(template.render(
            report_containers=self.report_containers,
            report_status=self.report_status,
            meta_tag=self.meta_tag,
            image=self.get_image_name(),
            bucket=self.bucket,
            region=self.region,
            screenshot=self.screenshot,
            total_runtime=f"{self.total_runtime:.2f}s",
            ))

    def badge_render(self) -> None:
        """Render the badge file for upload"""
        self.logger.info("Creating badge")
        try:
            badge = anybadge.Badge("CI", self.report_status, thresholds={
                                   "PASS": "green", "FAIL": "red"})
            badge.write_badge(f"{self.outdir}/badge.svg", overwrite=True)
            with open(f"{self.outdir}/ci-status.yml", "w", encoding="utf-8") as file:
                file.write(f"CI: '{self.report_status}'")
        except (ValueError,RuntimeError,FileNotFoundError,OSError):
            self.logger.exception("Failed to render badge file!")

    def json_render(self) -> None:
        """Create a JSON file of the report data."""
        self.logger.info("Creating report.json file")
        try:
            with open(f"{self.outdir}/report.json", mode="w", encoding="utf-8") as file:
                json.dump(self.report_containers, file, indent=2, sort_keys=True)
        except (OSError,FileNotFoundError,TypeError,Exception):
            self.logger.exception("Failed to render JSON file!")

    def report_upload(self) -> None:
        """Upload report files to S3

        Raises:
            Exception: S3UploadFailedError
            Exception: ValueError
            Exception: ClientError
        """
        self.logger.info("Uploading report files")
        try:
            shutil.copyfile(f"{os.path.dirname(os.path.realpath(__file__))}/404.jpg", f"{self.outdir}/404.jpg")
            shutil.copyfile(f"{os.path.dirname(os.path.realpath(__file__))}/logo.jpg", f"{self.outdir}/logo.jpg")
            shutil.copyfile(f"{os.path.dirname(os.path.realpath(__file__))}/favicon.ico", f"{self.outdir}/favicon.ico")
        except Exception:
            self.logger.exception("Failed to copy 404/favicon/logo file!")
        # Loop through files in outdir and upload
        for filename in os.listdir(self.outdir):
            time.sleep(0.5)
            ctype: tuple[str | None, str | None] = mimetypes.guess_type(filename.lower(), strict=False)
            ctype = {"ContentType": ctype[0] if ctype[0] else "text/plain", "ACL": "public-read", "CacheControl": "no-cache"}  # Set content types for files
            try:
                self.upload_file(f"{self.outdir}/{filename}", filename, ctype)
            except (S3UploadFailedError, ValueError, ClientError) as error:
                self.logger.exception("Upload Error!")
                self.log_upload()
                raise CIError(f"Upload Error: {error}") from error
        self.logger.info("Report available on https://%s/%s/%s/index.html",self.bucket, self.image, self.meta_tag)

    def create_html_ansi_file(self, blob:str, tag:str, name:str, full:bool = True) -> None:
        """Creates an HTML file in the "self.outdir" directory that we upload to S3

        Args:
            blob (str): The blob you want to convert
            tag (str): The tag we are testing
            name (str): The name of the file. File name will be `{tag}.{name}.html`
            full (bool): Whether to include the full HTML document or only the body.

        """
        try:
            self.logger.info("Creating %s.%s.html", tag, name)
            converter = Ansi2HTMLConverter(title=f"{tag}-{name}")
            html_logs: str = converter.convert(blob,full=full)
            with open(f"{self.outdir}/{tag}.{name}.html", "w", encoding="utf-8") as file:
                file.write(html_logs)
        except Exception:
            self.logger.exception("Failed to create %s.%s.html", tag,name)

    @testing
    def upload_file(self, file_path:str, object_name:str, content_type:dict) -> None:
        """Upload a file to an S3 bucket.

        The file is uploaded to two directories in the bucket, one for the meta tag and one for the release tag.

        e.g. `https://ci-tests.linuxserver.io/linuxserver/plex/1.40.5.8921-836b34c27-ls233/index.html` and `https://ci-tests.linuxserver.io/linuxserver/plex/latest/index.html`

        Args:
            file_path (str): File to upload
            object_name (str): S3 object name.
            content_type (dict): Content type for the file
        """
        self.logger.info("Uploading %s to %s bucket",file_path, self.bucket)
        meta_dir: str = f"{self.image}/{self.meta_tag}"
        release_dir: str = f"{self.image}/{self.release_tag}"
        self.s3_client.upload_file(file_path, self.bucket, f"{meta_dir}/{object_name}", ExtraArgs=content_type)
        self.s3_client.upload_file(file_path, self.bucket, f"{release_dir}/{object_name}", ExtraArgs=content_type)

    def log_upload(self) -> None:
        """Upload the ci.log to S3..."""
        self.logger.info("Uploading logs")
        try:
            log_source_path = "ci.log"
            log_dest_path = f"{self.outdir}/ci.log"

            if not os.path.exists(log_dest_path):
                shutil.copyfile(log_source_path, log_dest_path)

            self.upload_file(log_dest_path, "ci.log", {"ContentType": "text/plain", "ACL": "public-read"})
            with open(log_dest_path, "r", encoding="utf-8") as logs:
                blob: str = logs.read()
                self.create_html_ansi_file(blob, "python", "log")
                self.upload_file(f"{self.outdir}/python.log.html", "python.log.html", {"ContentType": "text/html", "ACL": "public-read"})
        except (S3UploadFailedError, ClientError, FileNotFoundError) as e:
            self.logger.exception(f"Failed to upload the CI logs! Error: {e}")

    def _add_test_result(self, tag:str, test:str, status:str, message:str, start_time:float|int = 0.0) -> None:
        """Add a test result to the report

        Args:
            tag (str): The tag we are testing
            test (str): The test we are running
            status (str): The status of the test
            message (str): The message of the test
            start_time (str, optional): The start time of the test. Defaults to 0.0. Used to calculate the runtime of the test.
        """
        if status not in ["PASS","FAIL"]:
            raise ValueError("Status must be either PASS or FAIL")
        if tag not in self.tags:
            raise ValueError("Tag not in the list of tags")
        if not start_time:
            runtime = "-"
        if isinstance(start_time,(float, int)):
            runtime: str = f"{time.time() - start_time:.2f}s"
        self.tag_report_tests[tag]["test"][test] = (dict(sorted({
            "status":status,
            "message":message,
            "runtime": runtime}.items())))

    def take_screenshot(self, container: Container, tag:str) -> tuple[bool, str]:
        """Take a screenshot and save it to self.outdir if self.screenshot is True

        Takes a screenshot using a ChromiumDriver instance.

        Args:
            container (Container): Container object
            tag (str): The container tag we are testing.

        Returns:
            tuple[bool, str]: Return (True, browser_logs) if successful, otherwise (False, browser_logs).
        """
        if not self.screenshot:
            return True, ""
        proto: Literal["https", "http"] = "https" if self.ssl.upper() == "TRUE" else "http"
        screenshot_timeout = time.time() + self.screenshot_timeout
        test = "Get screenshot"
        start_time = time.time()
        driver: WebDriver | None = None
        browser_logs: str = ""
        try:
            driver = self.setup_driver()
            container.reload()
            ip_adr:str = container.attrs.get("NetworkSettings",{}).get("Networks",{}).get("bridge",{}).get("IPAddress","")
            webauth: str = f"{self.webauth}"
            endpoint: str = f"{proto}://{webauth}{ip_adr}:{self.port}{self.webpath}"
            self.logger.info("Trying for %s seconds to take a screenshot of %s ",self.screenshot_timeout, tag)
            while time.time() < screenshot_timeout:
                try:
                    if not self._check_response(endpoint):
                        raise requests.ConnectionError("Bad response")
                    driver.get(endpoint)
                    time.sleep(self.screenshot_delay) # A grace period for the page to load
                    self.logger.debug("Trying to take screenshot of %s at %s", tag, endpoint)
                    driver.get_screenshot_as_file(f"{self.outdir}/{tag}.png")
                    if not os.path.isfile(f"{self.outdir}/{tag}.png"):
                        raise FileNotFoundError(f"Screenshot '{self.outdir}/{tag}.png' not found")
                    self._add_test_result(tag, test, "PASS", "-", start_time)
                    self.logger.success("Screenshot %s: PASSED after %.2f seconds", tag, time.time() - start_time)
                    return True, self._get_browser_logs(driver, tag)
                except Exception as error:
                    logger.debug("Failed to take screenshot of %s at %s, trying again in 3 seconds", tag, endpoint, exc_info=error)
                    time.sleep(3)
                    if time.time() >= screenshot_timeout:
                        self.logger.error("Failed to take screenshot of %s at %s", tag, endpoint)
                        raise error
            raise TimeoutException("Timeout taking screenshot")
        except (requests.Timeout, requests.ConnectionError, KeyError) as error:
            self._add_test_result(tag, test, "FAIL", f"CONNECTION ERROR: {str(error)}", start_time)
            self.logger.exception("Screenshot %s FAIL CONNECTION ERROR", tag)
            self.report_status = "FAIL"
            if driver:
                browser_logs = self._get_browser_logs(driver, tag)
            return False, browser_logs
        except TimeoutException as error:
            self._add_test_result(tag, test, "FAIL", f"TIMEOUT: {str(error)}", start_time)
            self.logger.exception("Screenshot %s FAIL TIMEOUT", tag)
            self.report_status = "FAIL"
            if driver:
                browser_logs = self._get_browser_logs(driver, tag)
            return False, browser_logs
        except (WebDriverException, Exception) as error:
            self._add_test_result(tag, test, "FAIL", f"UNKNOWN: {str(error)}", start_time)
            self.logger.exception("Screenshot %s FAIL UNKNOWN", tag)
            self.report_status = "FAIL"
            if driver:
                browser_logs = self._get_browser_logs(driver, tag)
            return False, browser_logs
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    self.logger.exception("Failed to quit the driver")

    def _check_response(self, endpoint:str) -> bool:
        """Check if we can get a good response from the endpoint

        Args:
            endpoint (str): The endpoint we are testing

        Returns:
            bool: Return True if we get a good response, otherwise False.
        """
        try:
            self.logger.debug("Checking response on %s", endpoint)
            response = requests.get(endpoint, timeout=10, verify=False)
            response.raise_for_status()
            return True
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError, requests.RequestException) as exc:
            self.logger.warning("Failed to get a good response on %s", endpoint, exc_info=exc)
            return False

    @deprecated(reason="Use the chrome driver directly instead")
    def start_tester(self, proto:str, endpoint:str, tag:str) -> tuple[Container,str]:
        """Spin up an RDP test container to load the container web ui.

        Args:
            `proto` (str): The protocol to use for the endpoint.
            `endpoint` (str): The container endpoint to use with the tester container.
            `tag` (str): The container tag

        Returns:
            Container/str: Returns the tester Container object and the tester endpoint
        """
        self.logger.info("Starting tester container for tag: %s", tag)
        testercontainer: Container = self.client.containers.run("ghcr.io/linuxserver/tester:latest",
                                                     shm_size="1G",
                                                     security_opt=["seccomp=unconfined"],
                                                     detach=True,
                                                     environment={"URL": endpoint})
        #Sleep for the user specified amount of time
        self.logger.info("Sleeping for %s seconds before reloading %s and refreshing container attrs on %s run", self.test_container_delay, testercontainer.image, tag)
        time.sleep(int(self.test_container_delay))
        testercontainer.reload()
        testerip: str = testercontainer.attrs["NetworkSettings"]["Networks"]["bridge"]["IPAddress"]
        testerendpoint: str = f"http://{testerip}:3000"
        session = requests.Session()
        retries = Retry(total=10, backoff_factor=2,status_forcelist=[502, 503, 504])
        session.mount(proto, HTTPAdapter(max_retries=retries))
        session.get(testerendpoint)
        return testercontainer, testerendpoint


    def setup_driver(self) -> WebDriver:
        """Return a single ChromiumDriver object the class can use

        Returns:
            Webdriver: Returns a Chromedriver object
        """
        self.logger.info("Init Chromedriver")
        # Selenium webdriver options
        chrome_options = webdriver.ChromeOptions()
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--disable-extensions")
        chrome_options.add_argument("--ignore-certificate-errors")
        chrome_options.set_capability("goog:loggingPrefs", {"browser": "ALL"})
        driver = webdriver.Chrome(options=chrome_options)
        driver.set_page_load_timeout(60)
        driver.set_window_size(1920,1080)
        return driver

    @testing
    def create_s3_client(self) -> boto3.client:
        """Create and return an s3 client object

        Returns:
            Session.client: An S3 client.
        """
        s3_client = boto3.Session().client(
                "s3",
                region_name=self.region,
                aws_access_key_id=self.s3_key,
                aws_secret_access_key=self.s3_secret)
        return s3_client

    def create_docker_client(self) -> DockerClient|None:
        """Create and return a docker client object

        Returns:
            DockerClient: A docker client object
        """
        try:
            return docker.from_env()
        except Exception:
            self.logger.error("Failed to create Docker client!")


class CIError(Exception):
    pass
