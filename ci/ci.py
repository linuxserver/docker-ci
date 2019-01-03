#!/usr/bin/env python

import os
import boto3
import time
import sys
import docker
import requests
import anybadge
from multiprocessing.pool import Pool
from selenium import webdriver
from selenium.common.exceptions import ErrorInResponseException,TimeoutException
from jinja2 import Template
client = docker.from_env()
session = boto3.session.Session()

# Global Vars
global report_status
global report_tests
global report_containers
report_tests = []
report_containers = []
report_status = 'PASS'

#############
# Functions #
#############

# If the tests cannot even be run just fail the job
def core_fail(message):
    print(message)
    sys.exit(1)

# Convert env input to dictionary
def convert_env(vars):
    global dockerenv
    dockerenv = {}
    try:
        if '|' in vars:
            for varpair in vars.split('|'):
                var = varpair.split('=')
                dockerenv[var[0]] = var[1]
        else:
            var = vars.split('=')
            dockerenv[var[0]] = var[1]
    except Exception as error:
        core_fail(str(error))

# Update global variables from threaded testing process
def update_globals(data):
    global report_status
    for (tests,containers,status) in data:
        for test in tests:
           report_tests.append(test)
        for container in containers:
           report_containers.append(container)
        if status == 'FAIL':
           report_status = 'FAIL'

# Set the optional parameters
global webauth
global webpath
global dockerenv
global region
global bucket
global screenshot
global port
global ssl
global testdelay
try:
    webauth = os.environ["WEB_AUTH"]
except KeyError:
    webauth = 'user:password'
try:
    webpath = os.environ["WEB_PATH"]
except KeyError:
    webpath = ''
try:
    convert_env(os.environ["DOCKER_ENV"])
except KeyError:
    dockerenv = {}
try:
    region = os.environ["DO_REGION"]
except KeyError:
    region = 'ams3'
try:
    bucket = os.environ["DO_BUCKET"]
except KeyError:
    bucket = 'lsio-ci'
try:
    screenshot = os.environ["WEB_SCREENSHOT"]
except KeyError:
    screenshot = 'false'
try:
    port = os.environ["PORT"]
except KeyError:
    port = '80'
try:
    ssl = os.environ["SSL"]
except KeyError:
    ssl = 'false'
try:
    testdelay = os.environ["DELAY_START"]
except KeyError:
    testdelay = '5'

# Make sure all needed env variables are set
def check_env():
    try:
        global image
        global tags
        global meta_tag
        global base
        global spaces_key
        global spaces_secret
        image = os.environ["IMAGE"]
        base = os.environ["BASE"]
        spaces_key = os.environ["ACCESS_KEY"]
        spaces_secret = os.environ["SECRET_KEY"]
        meta_tag = os.environ["META_TAG"]
        tags_env = os.environ["TAGS"]
        tags = []
        if '|' in tags_env:
            for tag in tags_env.split('|'):
                tags.append(tag)
        else:
            tags.append(tags_env)
    except KeyError as error:
        core_fail(str(error) + ' is not set in ENV')

# Create output path
def  create_dir():
    global outdir
    outdir = os.path.dirname(os.path.realpath(__file__)) + '/output/' + image + '/' + meta_tag + '/'
    try:
        os.stat(outdir)
    except:
        os.makedirs(outdir)

# Main container test logic
def container_test(tag):
    # Vars for the threaded process
    report_tests = []
    report_containers = []
    report_status = 'PASS'
    # End the test with as much info as we have
    def endtest(container,report_tests,report_containers,report_status,tag,build_version,packages):
        logblob = container.logs().decode("utf-8")
        container.remove(force='true')
        # Add the info to the report
        report_containers.append({
        "tag":tag,
        "logs":logblob,
        "sysinfo":packages,
        "build_version":build_version
        })
        return (report_tests,report_containers,report_status)
    # Start the container
    print('Starting ' + tag)
    container = client.containers.run(image + ':' + tag,
        detach=True,
        environment=dockerenv)
    # Watch the logs for no more than 5 minutes
    t_end = time.time() + 60 * 5
    logsfound = False
    while time.time() < t_end:
        try:
            logblob = container.logs().decode("utf-8")
            if '[services.d] done.' in logblob:
                logsfound = True
                break
            time.sleep(1)
        except Exception as error:
            print('Startup failed for ' + tag)
            report_tests.append(['Startup ' + tag,'FAIL INIT NOT FINISHED'])
            report_status = 'FAIL'
            (report_tests,report_containers,report_status) = endtest(container,report_tests,report_containers,report_status,tag,'ERROR','ERROR')
            return (report_tests,report_containers,report_status)
    # Grab build version
    try:
        build_version = container.attrs["Config"]["Labels"]["build_version"]
        report_tests.append(['Get Build Version ' + tag,'PASS'])
    except Exception as error:
        build_version = 'ERROR'
        report_tests.append(['Get Build Version ' + tag,'FAIL'])
        report_status = 'FAIL'
        (report_tests,report_containers,report_status) = endtest(container,report_tests,report_containers,report_status,tag,build_version,'ERROR')
        return (report_tests,report_containers,report_status)
    # Check if the startup marker was found in the logs during the 2 minute spinup
    if logsfound == True:
        print('Startup completed for ' + tag)
        report_tests.append(['Startup ' + tag,'PASS'])
    elif logsfound == False:
        print('Startup failed for ' + tag)
        report_tests.append(['Startup ' + tag,'FAIL INIT NOT FINISHED'])
        report_status = 'FAIL'
        (report_tests,report_containers,report_status) = endtest(container,report_tests,report_containers,report_status,tag,build_version,'ERROR')
        return (report_tests,report_containers,report_status)
    # Dump package information
    print('Dumping package info for ' + tag)
    if base == 'alpine':
        command = 'apk info -v'
    elif base == 'debian' or base == 'ubuntu':
        command = 'apt list'
    try:
        info = container.exec_run(command)
        packages = info[1].decode("utf-8")
        report_tests.append(['Dump Versions ' + tag,'PASS'])
        print('Got Package info for ' + tag)
    except Exception as error:
        packages = 'ERROR'
        print(str(error))
        report_tests.append(['Dump Versions ' + tag,'FAIL'])
        report_status = 'FAIL'
        (report_tests,report_containers,report_status) = endtest(container,report_tests,report_containers,report_status,tag,build_version,packages)
        return (report_tests,report_containers,report_status)
    # Screenshot web interface and check connectivity
    if screenshot == 'true':
        # Sleep for the user specified amount of time
        time.sleep(int(testdelay))
        # Take a screenshot
        if ssl == 'true':
            proto = 'https://'
        else:
            proto = 'http://'
        container.reload()
        ip = container.attrs["NetworkSettings"]["Networks"]["bridge"]["IPAddress"]
        endpoint = proto + webauth + '@' + ip + ':' + port + webpath
        print('Taking screenshot of ' + tag + ' at ' + endpoint)
        try:
            # Selenium webdriver options
            chrome_options = webdriver.ChromeOptions()
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--headless')
            chrome_options.add_argument('--disable-gpu')
            chrome_options.add_argument('--window-size=1920x1080')
            driver = webdriver.Chrome(chrome_options=chrome_options)
            driver.set_page_load_timeout(10)
            requests.get(endpoint, timeout=3)
            driver.get(endpoint)
            driver.get_screenshot_as_file(outdir + tag + '.png')
            report_tests.append(['Screenshot ' + tag,'PASS'])
            # Quit selenium webdriver
            driver.quit()
        except (requests.Timeout, requests.ConnectionError, KeyError) as e:
            report_tests.append(['Screenshot ' + tag,'FAIL CONNECTION ERROR'])
        except ErrorInResponseException as error:
            report_tests.append(['Screenshot ' + tag,'FAIL SERVER ERROR'])
        except TimeoutException as error:
            report_tests.append(['Screenshot ' + tag,'FAIL TIMEOUT'])
        except WebDriverException as error:
            report_tests.append(['Screenshot ' + tag,'FAIL UNKNOWN'])
    # If all info is present end test
    (report_tests,report_containers,report_status) = endtest(container,report_tests,report_containers,report_status,tag,build_version,packages)
    return (report_tests,report_containers,report_status)

# Render the markdown file for upload
def report_render():
    print('Rendering Report')
    with open(os.path.dirname(os.path.realpath(__file__)) + '/results.template') as file_:
        template = Template(file_.read())
    markdown = template.render(
        report_tests=report_tests,
        report_containers=report_containers,
        report_status=report_status,
        meta_tag=meta_tag,
        image=image,
        bucket=bucket,
        region=region,
        screenshot=screenshot)
    with open(outdir + 'report.md', 'w') as f:
        f.write(markdown)

# Render the badge file for upload
def badge_render():
    try:
        badge = anybadge.Badge('CI', report_status, thresholds={'PASS': 'green', 'FAIL': 'red'})
        badge.write_badge(outdir + 'badge.svg')
    except Exception as error:
        print(error)

# Upload report to DO Spaces
def report_upload():
    print('Uploading Report')
    destination_dir = image + '/' + meta_tag + '/'
    latest_dir = image + '/latest/'
    spaces = session.client(
        's3',
        region_name=region,
        endpoint_url='https://' + region + '.digitaloceanspaces.com',
        aws_access_key_id=spaces_key,
        aws_secret_access_key=spaces_secret)
    # Index file upload
    index_file = os.path.dirname(os.path.realpath(__file__)) + '/index.html'
    try:
        spaces.upload_file(
            index_file,
            bucket,
            destination_dir + 'index.html',
            ExtraArgs={'ContentType': "text/html", 'ACL': "public-read"})
        spaces.upload_file(
            index_file,
            bucket,
            latest_dir + 'index.html',
            ExtraArgs={'ContentType': "text/html", 'ACL': "public-read"})
    except Exception as error:
        core_fail('Upload Error ' + str(error))
    # Loop for all others
    for filename in os.listdir(outdir):
        # Set content types for files
        if filename.lower().endswith('.svg'):
            CT = 'image/svg+xml'
        elif filename.lower().endswith('.png'):
            CT = 'image/png'
        elif filename.lower().endswith('.md'):
            CT = 'text/markdown'
        try:
            spaces.upload_file(
                outdir + filename,
                bucket,
                destination_dir + filename,
                ExtraArgs={'ContentType': CT,'ACL': "public-read",'CacheControl': 'no-cache'})
            spaces.upload_file(
                outdir + filename,
                bucket,
                latest_dir + filename,
                ExtraArgs={'ContentType': CT,'ACL': "public-read",'CacheControl': 'no-cache'})
        except Exception as error:
            core_fail('Upload Error ' + str(error))


##################
# Test Run Logic #
##################
check_env()
create_dir()
# Run through all the tags
pool=Pool(processes=3)
r = pool.map_async(container_test, tags, callback=update_globals)
r.wait()
report_render()
badge_render()
report_upload()
# Exit based on test results
if report_status == 'PASS':
    print('Tests Passed exiting 0')
    sys.exit(0)
elif report_status == 'FAIL':
    print('Tests Failed exiting 1')
    sys.exit(1)
