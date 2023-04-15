#!/usr/bin/env python3

from multiprocessing.pool import ThreadPool
from threading import current_thread
import os
import shutil
import time
import logging
from logging import Logger
import mimetypes
import requests
import json
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from functools import wraps
from typing import Callable, Any, Literal

import boto3
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
    def wrapper(*args, **kwargs):
        if os.environ.get("DRY_RUN") == "true":
            logger.warning("Dry run enabled, skipping execution of %s", func.__name__)
            return
        return func(*args,**kwargs)
    return wrapper

class SetEnvs():
    """Simple helper class that sets up the ENVs"""
    def __init__(self) -> None:
        self.logger: Logger = logging.getLogger("SetEnvs")

        os.environ['S6_VERBOSITY'] = os.environ.get("CI_S6_VERBOSITY","2")
        # Set the optional parameters
        self.dockerenv: dict[str, str] = self.convert_env(os.environ.get("DOCKER_ENV", ""))
        self.webauth: str = os.environ.get('WEB_AUTH', 'user:password')
        self.webpath: str = os.environ.get('WEB_PATH', '')
        self.screenshot: str = os.environ.get('WEB_SCREENSHOT', 'false')
        self.screenshot_delay: str = os.environ.get('WEB_SCREENSHOT_DELAY', '30')
        self.logs_delay: str = os.environ.get('DOCKER_LOGS_DELAY', '300')
        self.port: str = os.environ.get('PORT', '80')
        self.ssl: str = os.environ.get('SSL', 'false')
        self.region: str = os.environ.get('S3_REGION', 'us-east-1')
        self.bucket: str = os.environ.get('S3_BUCKET', 'ci-tests.linuxserver.io')
        self.test_container_delay: str = os.environ.get('DELAY_START', '5')
        self.check_env()


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
            self.logger.info("Converting envs")
            try:
                if '|' in envs:
                    for varpair in envs.split('|'):
                        var: list[str] = varpair.split('=')
                        env_dict[var[0]] = var[1]
                else:
                    var = envs.split('=')
                    env_dict[var[0]] = var[1]
                env_dict["S6_VERBOSITY"] = os.environ.get('S6_VERBOSITY')
            except Exception as error:
                self.logger.exception("Failed to convert DOCKER_ENV: %s to dictionary!", envs)
                raise CIError(f"Failed converting DOCKER_ENV: {envs} to dictionary") from error
        return env_dict


    def check_env(self) -> None:
        """Make sure all needed ENVs are set

        Raises:
            CIError: Raises a CIError exception if one of the enviroment values is not set.
        """
        try:
            self.image: str = os.environ['IMAGE']
            self.base: str = os.environ['BASE']
            self.s3_key: str = os.environ['ACCESS_KEY']
            self.s3_secret: str = os.environ['SECRET_KEY']
            self.meta_tag: str = os.environ['META_TAG']
            self.tags_env: str = os.environ['TAGS']
        except KeyError as error:
            self.logger.exception("Key is not set in ENV!")
            raise CIError(f'Key {error} is not set in ENV!') from error


class CI(SetEnvs):
    """CI object to use for testing image tags.

    Args:
        SetEnvs (Object): Helper class that initializes and checks that all the necessary enviroment variables exists. Object is initialized upon init of CI.
    """
    def __init__(self) -> None:
        super().__init__()  # Init the SetEnvs object.
        self.logger = logging.getLogger("LSIO CI")
        logging.getLogger("botocore.auth").setLevel(logging.INFO)  # Don't log the S3 authentication steps.

        self.client: DockerClient = docker.from_env()
        self.tags = list(self.tags_env.split('|'))
        self.tag_report_tests:dict[str,dict[str,dict]] = {tag: {'test':{}} for tag in self.tags} # Adds all the tags as keys with an empty dict as value to the dict
        self.report_containers: dict[str,dict[str,dict]] = {}
        self.report_status = 'PASS'
        self.outdir: str = f'{os.path.dirname(os.path.realpath(__file__))}/output/{self.image}/{self.meta_tag}'
        os.makedirs(self.outdir, exist_ok=True)
        self.s3_client = self.create_s3_client()

    def run(self,tags: list) -> None:
        """Will iterate over all the tags running container_test() on each tag, multithreaded.

        Also does a pull of the linuxserver/tester:latest image before running container_test.

        Args:
            `tags` (list): All the tags we will test on the image.

        """
        self.logger.info("Pulling ghcr.io/linuxserver/tester:latest")
        self.client.images.pull(repository="ghcr.io/linuxserver/tester", tag="latest") # Pulls latest tester image. 
        thread_pool = ThreadPool(processes=10)
        thread_pool.map(self.container_test,tags)
        display = Display(size=(1920, 1080)) # Setup an x virtual frame buffer (Xvfb) that Selenium can use during the tests.
        display.start()
        thread_pool.close()
        thread_pool.join()
        display.stop()


    def container_test(self, tag: str) -> None:
        """Main container test logic.

        Args:
            `tag` (str): The container tag

        1. Spins up the container tag
            Checks the container logs for either `[services.d] done.` or `[ls.io-init] done.`
        2. Export the build version from the Container object.
        3. Export the package info from the Container object.
        4. Take a screenshot for the report.
        5. Add report information to report.json.
        """
        # Name the thread for easier debugging.
        current_thread().name = f"{self.get_platform(tag).upper()}Thread"

        # Start the container
        self.logger.info('Starting test of: %s', tag)
        container: Container = self.client.containers.run(f'{self.image}:{tag}',
                                               detach=True,
                                               environment=self.dockerenv)
        container_config: list[str] = container.attrs["Config"]["Env"]
        self.logger.info("Container config of tag %s: %s",tag,container_config)


        logsfound: bool = self.watch_container_logs(container, tag) # Watch the logs for no more than 5 minutes
        if not logsfound:
            self._endtest(container, tag, "ERROR", "ERROR", False)
            return

        build_version: str = self.get_build_version(container,tag) # Get the image build version
        if build_version == "ERROR":
            self._endtest(container, tag, build_version, "ERROR", False)
            return

        sbom: str = self.generate_sbom(tag)
        if sbom == "ERROR":
            self._endtest(container, tag, build_version, sbom, False)
            return

        # Screenshot web interface and check connectivity
        if self.screenshot == 'true':
            self.take_screenshot(container, tag)

        self._endtest(container, tag, build_version, sbom, True)
        self.logger.info("Testing of %s PASSED", tag)
        return

    def _endtest(self: 'CI', container:Container, tag:str, build_version:str, packages:str, test_success: bool) -> None:
        """End the test with as much info as we have and append to the report.

        Args:
            `container` (Container): Container object
            `tag` (str): The container tag
            `build_version` (str): The Container build version
            `packages` (str): SBOM dump from the container
            `test_success` (bool): If the testing of the container failed or not
        """
        logblob: Any = container.logs().decode('utf-8')
        self.create_html_ansi_file(logblob, tag, "log") # Generate html container log file based on the latest logs
        try:
            container.remove(force='true')
        except APIError:
            self.logger.exception("Failed to remove container %s",tag)
        warning_texts: dict[str, str] = {
            "dotnet": "May be a .NET app. Service might not start on ARM32 with QEMU",
            "uwsgi": "This image uses uWSGI and might not start on ARM/QEMU"
        }
        # Add the info to the report
        self.report_containers[tag] = {
            'logs': logblob,
            'sysinfo': packages,
            'warnings': {
                'dotnet': warning_texts["dotnet"] if "icu-libs" in packages and "arm32" in tag else "",
                'uwsgi': warning_texts["uwsgi"] if "uwsgi" in packages and "arm" in tag else ""
            },
            'build_version': build_version,
            'test_results': self.tag_report_tests[tag]['test'],
            'test_success': test_success,
            }
        self.report_containers[tag]["has_warnings"] = any(warning[1] for warning in self.report_containers[tag]["warnings"].items())

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
            case _:
                return "amd64"

    def export_package_info(self, container:Container, tag:str) -> str:
        """Dump the package info into a string for the report

        Args:
            container (Container): The container we are testing
            tag (str): The tag we are testing

        Returns:
            str: Return the output of the dump command or 'ERROR'
        """
        # Dump package information
        dump_commands: dict[str, str] = {
            'alpine': 'apk info -v',
            'debian': 'apt list',
            'ubuntu': 'apt list',
            'fedora': 'rpm -qa',
            'arch': 'pacman -Q'
            }
        try:
            self.logger.info('Dumping package info for %s',tag)
            info: ExecResult = container.exec_run(dump_commands[self.base])
            packages: str = info[1].decode('utf-8')
            if info[0] != 0:
                raise CIError(f"Failed to dump packages. Output: {packages}")
            self.tag_report_tests[tag]['test']['Dump package info'] = (dict(sorted({
                'status':'PASS',
                'message':'-'}.items())))
            self.logger.info('Dump package info %s: PASS', tag)
        except (APIError, IndexError,CIError) as error:
            packages = 'ERROR'
            self.logger.exception('Dumping package info on %s: FAIL', tag)
            self.tag_report_tests[tag]['test']['Dump package info'] = (dict(sorted({
                'Dump package info':'FAIL',
                'message':str(error)}.items())))
            self.report_status = 'FAIL' 
        return packages

    def generate_sbom(self, tag:str) -> str:
        """Generate the SBOM for the image tag.

        Creates the output file in `{self.outdir}/{tag}.sbom.html`

        Args:
            tag (str): The tag we are testing

        Returns:
            bool: Return the output if successful otherwise "ERROR".
        """
        platform: str = self.get_platform(tag)
        syft:Container = self.client.containers.run(image="ghcr.io/anchore/syft:v0.76.1",command=f"{self.image}:{tag} --platform=linux/{platform}", 
            detach=True, volumes={"/var/run/docker.sock": {"bind": "/var/run/docker.sock", "mode": "rw"}})
        self.logger.info('Creating SBOM package list on %s',tag)

        t_end: float = time.time() + int(self.logs_delay)
        self.logger.info("Tailing the syft container logs for %s seconds looking the 'VERSION' message on tag: %s",self.logs_delay,tag)
        error_message = "Did not find the 'VERSION' keyword in the syft container logs"
        while time.time() < t_end:
            time.sleep(5)
            try:
                logblob: str = syft.logs().decode('utf-8')
                if 'VERSION' in logblob:
                    self.logger.info('Get package versions for %s completed', tag)
                    self.tag_report_tests[tag]['test']['Create SBOM'] = (dict(sorted({
                        'status':'PASS',
                        'message':'-'}.items())))
                    self.logger.info('Create SBOM package list %s: PASS', tag)
                    self.create_html_ansi_file(str(logblob),tag,"sbom")
                    try:
                        syft.remove(force=True)
                    except Exception:
                        self.logger.exception("Failed to remove the syft container, %s",tag)
                    return logblob
            except (APIError,ContainerError,ImageNotFound) as error:
                error_message: APIError | ContainerError | ImageNotFound = error
                self.logger.exception('Creating SBOM package list on %s: FAIL', tag)
        self.logger.error("Failed to generate SBOM output on tag %s. SBOM output:\n%s",tag, logblob)
        self.report_status = "FAIL"
        self.tag_report_tests[tag]['test']['Create SBOM'] = (dict(sorted({
            "Create SBOM":"FAIL",
            "message":str(error_message)}.items())))
        try:
            syft.remove(force=True)
        except Exception:
            self.logger.exception("Failed to remove the syft container, %s",tag)
        return "ERROR"

    def get_build_version(self,container:Container,tag:str) -> str:
        """Fetch the build version from the container object attributes.

        Args:
            container (Container): The container we are testing
            tag (str): The current tag we are testing

        Returns:
            str: Returns the build version or 'ERROR'
        """
        try:
            self.logger.info("Fetching build version on tag: %s",tag)
            build_version: str = container.attrs['Config']['Labels']['build_version']
            self.tag_report_tests[tag]['test']['Get build version'] = (dict(sorted({
                'status':'PASS',
                'message':'-'}.items())))
            self.logger.info('Get build version on tag "%s": PASS', tag)
        except (APIError,KeyError) as error:
            self.logger.exception('Get build version on tag "%s": FAIL', tag)
            build_version = 'ERROR'
            if isinstance(error,KeyError):
                error: str = f"KeyError: {error}"
            self.tag_report_tests[tag]['test']['Get build version'] = (dict(sorted({
                'status':'FAIL',
                'message':str(error)}.items())))
            self.report_status = 'FAIL'
        return build_version

    def watch_container_logs(self, container:Container, tag:str) -> bool:
        """Tail the container logs for 5 minutes and look for the init done message that tells us the container started up
        successfully.

        Args:
            container (Container): The container we are testing
            tag (str): The tag we are testing

        Returns:
            bool: Return True if the 'done' message is found, otherwise False.
        """
        t_end: float = time.time() + int(self.logs_delay)
        self.logger.info("Tailing the %s logs for %s seconds looking for the 'done' message", tag, self.logs_delay)
        while time.time() < t_end:
            try:
                logblob: str = container.logs().decode('utf-8')
                if '[services.d] done.' in logblob or '[ls.io-init] done.' in logblob:
                    self.logger.info('Container startup completed for %s', tag)
                    self.tag_report_tests[tag]['test']['Container startup'] = (dict(sorted({
                        'status':'PASS',
                        'message':'-'}.items())))
                    self.logger.info('Container startup %s: PASS', tag)
                    return True
                time.sleep(1)
            except APIError as error:
                self.logger.exception('Container startup %s: FAIL - INIT NOT FINISHED', tag)
                self.tag_report_tests[tag]['test']['Container startup'] = (dict(sorted({
                    'status':'FAIL',
                    'message': f'INIT NOT FINISHED: {str(error)}'
                    }.items())))
                self.report_status = 'FAIL'
                return False
        self.logger.error('Container startup failed for %s', tag)
        self.tag_report_tests[tag]['test']['Container startup'] = (dict(sorted({
            'status':'FAIL',
            'message':'INIT NOT FINISHED'}.items())))
        self.logger.error('Container startup %s: FAIL - INIT NOT FINISHED', tag)
        self.report_status = 'FAIL'
        return False

    def report_render(self) -> None:
        """Render the index file for upload"""
        self.logger.info('Rendering Report')
        env = Environment(autoescape=select_autoescape(enabled_extensions=('html', 'xml'),default_for_string=True),
                          loader = FileSystemLoader(os.path.dirname(os.path.realpath(__file__))) )
        template: Template = env.get_template('template.html')
        self.report_containers = json.loads(json.dumps(self.report_containers,sort_keys=True))
        with open(f'{self.outdir}/index.html', mode="w", encoding='utf-8') as file_:
            file_.write(template.render(
            report_containers=self.report_containers,
            report_status=self.report_status,
            meta_tag=self.meta_tag,
            image=self.image,
            bucket=self.bucket,
            region=self.region,
            screenshot=self.screenshot
            ))

    def badge_render(self) -> None:
        """Render the badge file for upload"""
        self.logger.info("Creating badge")
        try:
            badge = anybadge.Badge('CI', self.report_status, thresholds={
                                   'PASS': 'green', 'FAIL': 'red'})
            badge.write_badge(f'{self.outdir}/badge.svg')
            with open(f'{self.outdir}/ci-status.yml', 'w', encoding='utf-8') as file:
                file.write(f'CI: "{self.report_status}"')
        except (ValueError,RuntimeError,FileNotFoundError,OSError):
            self.logger.exception("Failed to render badge file!")

    def json_render(self) -> None:
        """Create a JSON file of the report data."""
        self.logger.info("Creating report.json file")
        try:
            with open(f'{self.outdir}/report.json', mode="w", encoding='utf-8') as file:
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
        self.logger.info('Uploading report files')
        try:
            shutil.copyfile(f'{os.path.dirname(os.path.realpath(__file__))}/404.jpg', f'{self.outdir}/404.jpg')
        except Exception:
            self.logger.exception("Failed to copy 404 file!")
        # Loop through files in outdir and upload
        for filename in os.listdir(self.outdir):
            time.sleep(0.5)
            ctype: tuple[str | None, str | None] = mimetypes.guess_type(filename.lower(), strict=False)
            ctype = {'ContentType': ctype[0] if ctype[0] else 'text/plain', 'ACL': 'public-read', 'CacheControl': 'no-cache'}  # Set content types for files
            try:
                self.upload_file(f'{self.outdir}/{filename}', filename, ctype)
            except (S3UploadFailedError, ValueError, ClientError) as error:
                self.logger.exception('Upload Error!')
                self.log_upload()
                raise CIError(f'Upload Error: {error}') from error
        self.logger.info('Report available on https://ci-tests.linuxserver.io/%s/index.html', f'{self.image}/{self.meta_tag}')

    def create_html_ansi_file(self, blob:str, tag:str, name:str, full:bool = True) -> None:
        """Creates an HTML file in the 'self.outdir' directory that we upload to S3

        Args:
            blob (str): The blob you want to convert
            tag (str): The tag we are testing
            name (str): The name of the file. File name will be `{tag}.{name}.html`
            full (bool): Whether to include the full HTML document or only the body.

        """
        try:
            self.logger.info(f"Creating {tag}.{name}.html")
            converter = Ansi2HTMLConverter(title=f"{tag}-{name}")
            html_logs: str = converter.convert(blob,full=full)
            with open(f'{self.outdir}/{tag}.{name}.html', 'w', encoding='utf-8') as file:
                file.write(html_logs)
        except Exception:
            self.logger.exception("Failed to create %s.%s.html", tag,name)

    @testing
    def upload_file(self, file_path:str, object_name:str, content_type:dict) -> None:
        """Upload a file to an S3 bucket

        Args:
            `file_path` (str): File to upload
            `bucket` (str): Bucket to upload to
            `object_name` (str): S3 object name.
        """
        self.logger.info('Uploading %s to %s bucket',file_path, self.bucket)
        destination_dir: str = f'{self.image}/{self.meta_tag}'
        latest_dir: str = f'{self.image}/latest'
        self.s3_client.upload_file(file_path, self.bucket, f'{destination_dir}/{object_name}', ExtraArgs=content_type)
        self.s3_client.upload_file(file_path, self.bucket, f'{latest_dir}/{object_name}', ExtraArgs=content_type)

    def log_upload(self) -> None:
        """Upload the ci.log to S3

        Raises:
            Exception: S3UploadFailedError
            Exception: ClientError
        """
        self.logger.info('Uploading logs')
        try:
            self.upload_file(f"{self.outdir}/ci.log", 'ci.log', {'ContentType': 'text/plain', 'ACL': 'public-read'})
            with open(f"{self.outdir}/ci.log","r", encoding='utf-8') as logs:
                blob: str = logs.read()
                self.create_html_ansi_file(blob,"python","log")
                self.upload_file(f"{self.outdir}/python.log.html", 'python.log.html', {'ContentType': 'text/html', 'ACL': 'public-read'})
        except (S3UploadFailedError, ClientError):
            self.logger.exception('Failed to upload the CI logs!')


    def take_screenshot(self, container: Container, tag:str) -> None:
        """Take a screenshot and save it to self.outdir

        Spins up an ghcr.io/linuxserver/tester container and takes a screenshot using Selenium.

        Args:
            `container` (Container): Container object
            `tag` (str): The container tag we are testing.
        """
        proto: Literal['https', 'http'] = 'https' if self.ssl.upper() == 'TRUE' else 'http'
        # Sleep for the user specified amount of time
        self.logger.info('Sleeping for %s seconds before reloading container: %s and refreshing container attrs', self.test_container_delay, container.image)
        time.sleep(int(self.test_container_delay))
        container.reload()
        try:
            ip_adr: str = container.attrs['NetworkSettings']['Networks']['bridge']['IPAddress']
            endpoint: str = f'{proto}://{self.webauth}@{ip_adr}:{self.port}{self.webpath}'
            testercontainer, test_endpoint = self.start_tester(proto,endpoint,tag)
            driver: WebDriver = self.setup_driver()
            driver.get(test_endpoint)
            self.logger.info('Sleeping for %s seconds before creating a screenshot on %s', self.screenshot_delay, tag)
            time.sleep(int(self.screenshot_delay))
            self.logger.info('Taking screenshot of %s at %s', tag, endpoint)
            driver.get_screenshot_as_file(f'{self.outdir}/{tag}.png')
            self.tag_report_tests[tag]['test']['Get screenshot'] = (dict(sorted({
                'status':'PASS',
                'message':'-'}.items())))
            self.logger.info('Screenshot %s: PASS', tag)
        except (requests.Timeout, requests.ConnectionError, KeyError) as error:
            self.tag_report_tests[tag]['test']['Get screenshot'] = (dict(sorted({
                'status':'FAIL',
                'message': f'CONNECTION ERROR: {str(error)}'}.items())))
            self.logger.exception('Screenshot %s FAIL CONNECTION ERROR', tag)
        except TimeoutException as error:
            self.tag_report_tests[tag]['test']['Get screenshot'] = (dict(sorted({
                'status':'FAIL',
                'message':f'TIMEOUT: {str(error)}'}.items())))
            self.logger.exception('Screenshot %s FAIL TIMEOUT', tag)
        except (WebDriverException, Exception) as error:
            self.tag_report_tests[tag]['test']['Get screenshot'] = (dict(sorted({
                'status':'FAIL',
                'message':f'UNKNOWN: {str(error)}'}.items())))
            self.logger.exception('Screenshot %s FAIL UNKNOWN', tag)
        finally:
            try:
                testercontainer.remove(force='true')
            except Exception:
                self.logger.exception("Failed to remove tester container")


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
        testercontainer: Container = self.client.containers.run('ghcr.io/linuxserver/tester:latest',
                                                     shm_size='1G',
                                                     security_opt=["seccomp=unconfined"],
                                                     detach=True,
                                                     environment={'URL': endpoint})
        #Sleep for the user specified amount of time
        self.logger.info('Sleeping for %s seconds before reloading %s and refreshing container attrs on %s run', self.test_container_delay, testercontainer.image, tag)
        time.sleep(int(self.test_container_delay))
        testercontainer.reload()
        testerip: str = testercontainer.attrs['NetworkSettings']['Networks']['bridge']['IPAddress']
        testerendpoint: str = f'http://{testerip}:3000'
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
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--headless')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--window-size=1920x1080')
        chrome_options.add_argument('--disable-extensions')
        chrome_options.add_argument('--ignore-certificate-errors')
        chrome_options.add_argument('--disable-dev-shm-usage')  # https://developers.google.com/web/tools/puppeteer/troubleshooting#tips
        driver = webdriver.Chrome(options=chrome_options)
        driver.set_page_load_timeout(60)
        return driver

    @testing
    def create_s3_client(self) -> boto3.client:
        """Create and return an s3 client object

        Returns:
            Session.client: An S3 client.
        """
        s3_client = boto3.Session().client(
                's3',
                region_name=self.region,
                aws_access_key_id=self.s3_key,
                aws_secret_access_key=self.s3_secret)
        return s3_client

class CIError(Exception):
    pass
