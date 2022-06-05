#!/usr/bin/env python3

import os
import time
import logging
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import boto3
import docker
import anybadge
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from jinja2 import Template


class CI():
    '''What's up doc'''

    def __init__(self):
        self.logger = logging.getLogger(__name__)

        self.client = docker.from_env()
        self.session = boto3.Session()
        self.report_tests = []
        self.report_containers = []
        self.report_status = 'PASS'

        # Set the optional parameters
        self.dockerenv = self.convert_env(os.environ.get("DOCKER_ENV", ""))
        self.webauth = os.environ.get('WEB_AUTH', 'user:password')
        self.webpath = os.environ.get('WEB_PATH', '')
        self.region = os.environ.get('S3_REGION', 'us-east-1')
        self.bucket = os.environ.get('S3_BUCKET', 'ci-tests.linuxserver.io')
        self.screenshot = os.environ.get('WEB_SCREENSHOT', 'false')
        self.port = os.environ.get('PORT', '80')
        self.ssl = os.environ.get('SSL', 'false')
        self.testdelay = os.environ.get('DELAY_START', '5')

        self.check_env()

        self.outdir = f'{os.path.dirname(os.path.realpath(__file__))}/output/{self.image}/{self.meta_tag}'
        os.makedirs(self.outdir, exist_ok=True)

    @staticmethod
    def convert_env(envs:str = None):
        '''Convert env input to dictionary'''
        env_dict = {}
        if envs:
            if '|' in envs:
                for varpair in envs.split('|'):
                    var = varpair.split('=')
                    env_dict[var[0]] = var[1]
            else:
                var = envs.split('=')
                env_dict[var[0]] = var[1]
        return env_dict


    def check_env(self):
        '''Make sure all needed env variables are set'''
        try:
            self.image = os.environ['IMAGE']
            self.base = os.environ['BASE']
            self.s3_key = os.environ['ACCESS_KEY']
            self.s3_secret = os.environ['SECRET_KEY']
            self.meta_tag = os.environ['META_TAG']
            self.tags_env = os.environ['TAGS']
            self.tags = []
            if '|' in self.tags_env:
                for tag in self.tags_env.split('|'):
                    self.tags.append(tag)
            else:
                self.tags.append(self.tags_env)
        except KeyError as error:
            raise Exception(f'Key {error} is not set in ENV!') from error

    def container_test(self, tag):
        '''Main container test logic'''

        def _endtest(self: CI, container, tag, build_version, packages):
            '''End the test with as much info as we have'''
            logblob = container.logs().decode('utf-8')
            container.remove(force='true')
            # Add the info to the report
            self.report_containers.append({
                'tag': tag,
                'logs': logblob,
                'sysinfo': packages,
                'build_version': build_version,
                'tag_tests': self.report_tests
            })
            self.report_tests = []
            return (self.report_tests, self.report_containers, self.report_status)
        # Start the container
        self.logger.info('Starting test of: %s', tag)
        container = self.client.containers.run(f'{self.image}:{tag}',
                                               detach=True,
                                               environment=self.dockerenv)
        # Watch the logs for no more than 5 minutes
        logsfound = False
        t_end = time.time() + 60 * 5
        while time.time() < t_end:
            try:
                logblob = container.logs().decode('utf-8')
                if '[services.d] done.' in logblob or '[ls.io-init] done.' in logblob:
                    logsfound = True
                    break
                time.sleep(1)
            except Exception:
                self.logger.exception('Startup failed for %s', tag)
                self.report_tests.append([f'Startup {tag}', 'FAIL INIT NOT FINISHED'])
                self.report_status = 'FAIL'
                _endtest(self, container, tag, 'ERROR', 'ERROR')
                return (self.report_tests, self.report_containers, self.report_status)
        # Grab build version
        try:
            build_version = container.attrs['Config']['Labels']['build_version']
            self.report_tests.append([f'Get Build Version {tag}', 'PASS'])
            self.logger.info('Get Build Version %s: PASS', tag)
        except Exception:
            build_version = 'ERROR'
            self.report_tests.append([f'Get Build Version {tag}', 'FAIL'])
            self.logger.info('Get Build Version %s: FAIL', tag)
            self.report_status = 'FAIL'
            _endtest(self, container, tag, build_version, 'ERROR')
            return (self.report_tests, self.report_containers, self.report_status)

        # Check if the startup marker was found in the logs during the 2 minute spinup
        if logsfound is True:
            self.logger.info('Startup completed for %s', tag)
            self.report_tests.append([f'Startup {tag}', 'PASS'])
            self.logger.info('Startup %s: PASS', tag)
        elif logsfound is False:
            self.logger.warning('Startup failed for %s', tag)
            self.report_tests.append([f'Startup {tag}', 'FAIL INIT NOT FINISHED'])
            self.logger.error('Startup %s: FAIL - INIT NOT FINISHED', tag)
            self.report_status = 'FAIL'
            _endtest(self, container, tag, build_version, 'ERROR')
            return (self.report_tests, self.report_containers, self.report_status)
        # Dump package information
        self.logger.info('Dumping package info for %s',tag)
        if self.base == 'alpine':
            command = 'apk info -v'
        elif self.base in ('debian', 'ubuntu'):
            command = 'apt list'
        elif self.base == 'fedora':
            command = 'rpm -qa'
        elif self.base == 'arch':
            command = 'pacman -Q'
        try:
            info = container.exec_run(command)
            packages = info[1].decode('utf-8')
            self.report_tests.append([f'Dump Versions {tag}', 'PASS'])
            self.logger.info('Dump Versions %s: PASS', tag)
        except Exception as error:
            packages = 'ERROR'
            self.logger.exception(str(error))
            self.report_tests.append([f'Dump Versions {tag}', 'FAIL'])
            self.logger.error('Dump Versions %s: FAIL', tag)
            self.report_status = 'FAIL'
            _endtest(self, container, tag, build_version, packages)
            return (self.report_tests, self.report_containers, self.report_status)
        # Sleep for the user specified amount of time
        time.sleep(int(self.testdelay))
        # Screenshot web interface and check connectivity
        if self.screenshot == 'true':
            self.take_screenshot(container, tag)
        # If all info is present end test
        _endtest(self, container, tag, build_version, packages)
        return (self.report_tests, self.report_containers, self.report_status)

    def report_render(self):
        '''Render the markdown file for upload'''
        self.logger.info('Rendering Report')
        with open(f'{os.path.dirname(os.path.realpath(__file__))}/results.template', encoding='utf-8') as file_:
            template = Template(file_.read())
        markdown = template.render(
            report_tests=self.report_tests,
            report_containers=self.report_containers,
            report_status=self.report_status,
            meta_tag=self.meta_tag,
            image=self.image,
            bucket=self.bucket,
            region=self.region,
            screenshot=self.screenshot)
        with open(f'{self.outdir}/report.md', 'w', encoding='utf-8') as file:
            file.write(markdown)

    def badge_render(self):
        '''Render the badge file for upload'''
        self.logger.info("Creating badge")
        try:
            badge = anybadge.Badge('CI', self.report_status, thresholds={
                                   'PASS': 'green', 'FAIL': 'red'})
            badge.write_badge(f'{self.outdir}/badge.svg')
            with open(f'{self.outdir}/ci-status.yml', 'w', encoding='utf-8') as file:
                file.write(f'CI: "{self.report_status}"')
        except Exception as error:
            self.logger.exception(error)

    def report_upload(self):
        '''Upload report to S3'''
        self.logger.info('Uploading Report')
        destination_dir = f'{self.image}/{self.meta_tag}'
        latest_dir = f'{self.image}/latest'
        s3_instance = self.session.client(
            's3',
            region_name=self.region,
            aws_access_key_id=self.s3_key,
            aws_secret_access_key=self.s3_secret)
        # Index file upload
        index_file = f'{os.path.dirname(os.path.realpath(__file__))}/index.html'
        try:
            s3_instance.upload_file(
                index_file,
                self.bucket,
                f'{destination_dir}/index.html',
                ExtraArgs={'ContentType': 'text/html', 'ACL': 'public-read'})
            s3_instance.upload_file(
                index_file,
                self.bucket,
                f'{latest_dir}/index.html',
                ExtraArgs={'ContentType': 'text/html', 'ACL': 'public-read'})
        except Exception as error:
            self.logger.exception('Upload Error: %s',error)
            raise Exception(f'Upload Error: {error}') from error
        # Loop for all others
        for filename in os.listdir(self.outdir):
            time.sleep(0.5)
            # Set content types for files
            if filename.lower().endswith('.svg'):
                ctype = 'image/svg+xml'
            elif filename.lower().endswith('.png'):
                ctype = 'image/png'
            elif filename.lower().endswith('.md'):
                ctype = 'text/markdown'
            elif filename.lower().endswith('.yml'):
                ctype = 'text/yaml'
            else:
                ctype = 'text/plain'
            try:
                s3_instance.upload_file(
                    f'{self.outdir}/{filename}',
                    self.bucket,
                    f'{destination_dir}/{filename}',
                    ExtraArgs={'ContentType': ctype, 'ACL': 'public-read', 'CacheControl': 'no-cache'})
                s3_instance.upload_file(
                    f'{self.outdir}/{filename}',
                    self.bucket,
                    f'{latest_dir}/{filename}',
                    ExtraArgs={'ContentType': ctype, 'ACL': 'public-read', 'CacheControl': 'no-cache'})
            except Exception as error:
                self.logger.exception('Upload Error: %s',error)
                raise Exception(f'Upload Error: {error}') from error
        self.logger.info("Report available on https://ci-tests.linuxserver.io/%s/index.html",destination_dir)

    def log_upload(self):
        '''Upload debug log to S3'''
        self.logger.info('Uploading logs')
        destination_dir = f'{self.image}/{self.meta_tag}'
        latest_dir = f'{self.image}/latest'
        s3_instance = self.session.client(
            's3',
            region_name=self.region,
            aws_access_key_id=self.s3_key,
            aws_secret_access_key=self.s3_secret)
        # Log file upload
        try:
            s3_instance.upload_file(
                '/debug.log',
                self.bucket,
                f'{destination_dir}/debug.log',
                ExtraArgs={'ContentType': 'text/plain', 'ACL': 'public-read'})
            s3_instance.upload_file(
                '/debug.log',
                self.bucket,
                f'{latest_dir}/debug.log',
                ExtraArgs={'ContentType': 'text/plain', 'ACL': 'public-read'})
        except Exception as error:
            self.logger.exception('Upload Error: %s',error)
            raise Exception(f'Upload Error: {error}') from error

    def take_screenshot(self, container, tag):
        '''Take a screenshot and save it to self.outdir'''
        proto = 'https' if self.ssl == 'true' else 'http'
        container.reload()
        ip = container.attrs['NetworkSettings']['Networks']['bridge']['IPAddress']
        endpoint = f'{proto}://{self.webauth}@{ip}:{self.port}{self.webpath}'
        self.logger.info('Taking screenshot of %s at %s', tag, endpoint)
        testercontainer = self.client.containers.run('lsiodev/tester:latest',
                                                     shm_size='1G',
                                                     detach=True,
                                                     environment={'URL': endpoint})
        time.sleep(30)
        testercontainer.reload()
        testerip = testercontainer.attrs['NetworkSettings']['Networks']['bridge']['IPAddress']
        testerendpoint = 'http://' + testerip + ':3000'
        try:
            # Selenium webdriver options
            chrome_options = webdriver.ChromeOptions()
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--headless')
            chrome_options.add_argument('--disable-gpu')
            chrome_options.add_argument('--window-size=1920x1080')
            driver = webdriver.Chrome(options=chrome_options)
            driver.set_page_load_timeout(60)
            session = requests.Session()
            retries = Retry(total=4, backoff_factor=2,
                            status_forcelist=[502, 503, 504])
            session.mount(proto, HTTPAdapter(max_retries=retries))
            session.get(testerendpoint)
            driver.get(testerendpoint)
            time.sleep(15)
            driver.get_screenshot_as_file(f'{self.outdir}/{tag}.png')
            self.report_tests.append([f'Screenshot {tag}', 'PASS'])
            self.logger.info('Screenshot %s: PASS', tag)
            # Quit selenium webdriver
            driver.quit()
        except (requests.Timeout, requests.ConnectionError, KeyError):
            self.report_tests.append(
                [f'Screenshot {tag}', 'FAIL CONNECTION ERROR'])
            self.logger.exception('Screenshot %s FAIL CONNECTION ERROR', tag)
        except TimeoutException:
            self.report_tests.append([f'Screenshot {tag}', 'FAIL TIMEOUT'])
            self.logger.exception('Screenshot %s FAIL TIMEOUT', tag)
        except WebDriverException as error:
            self.report_tests.append(
                [f'Screenshot {tag}', f'FAIL UNKNOWN: {error}'])
            self.logger.exception('Screenshot %s FAIL UNKNOWN: %s', tag, error)
        testercontainer.remove(force='true')
