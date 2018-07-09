#!/usr/bin/env python

import os
import boto3
import time
import sys
import docker
from selenium import webdriver
from selenium.common.exceptions import ErrorInResponseException,TimeoutException
from jinja2 import Template
client = docker.from_env()
session = boto3.session.Session()

# Selenium webdriver options
chrome_options = webdriver.ChromeOptions()
chrome_options.add_argument('--no-sandbox')
chrome_options.add_argument('--headless')
chrome_options.add_argument('--disable-gpu')
chrome_options.add_argument('--window-size=1920x1080')
driver = webdriver.Chrome(chrome_options=chrome_options)
driver.set_page_load_timeout(10)

# Global Vars
global report_tests
global report_containers
global report_status
report_tests = []
report_containers = []
report_status = 'pass'

#############
# Functions #
#############

# If the tests cannot even be run just fail the job
def core_fail(message):
    print(message)
    sys.exit(1)

# If any of the tests are marked failed do not push the resulting images
def mark_fail():
    report_status = 'fail'

# Make sure all needed env variables are set
def check_env():
    try:
        global image
        global testdelay
        global tags
        global meta_tag
        global port
        global ssl
        global base
        global spaces_key
        global spaces_secret
        image = os.environ["IMAGE"]
        testdelay = os.environ["DELAY_START"]
        port = os.environ["PORT"]
        ssl = os.environ["SSL"]
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

# Take a screenshot using the webdriver
def take_screenshot(endpoint,container_tag):
    try:
        driver.get(endpoint)
        driver.get_screenshot_as_file(outdir + container_tag + '.png')
        report_tests.append(['Screenshot ' + container_tag,'PASS'])
    except ErrorInResponseException as error:
        report_tests.append(['Screenshot ' + container_tag,'FAIL SERVER ERROR'])
        mark_fail()
    except TimeoutException as error:
        report_tests.append(['Screenshot ' + container_tag,'FAIL TIMEOUT'])
        mark_fail()

# Remove container forcefully
def remove_container(container):
    container.remove(force='true')

# Main container test logic
def container_test(tag):
    # Start the container
    container = client.containers.run(image + ':' + tag, detach=True,
        environment={
        "APP_URL":"_",
        "DB_CONNECTION":"sqlite_testing"
        })
    # Watch the logs for no more than 2 minutes
    t_end = time.time() + 60 * 2
    logsfound = False
    while time.time() < t_end:
        try:
            logblob = container.logs().decode("utf-8")
            if '[services.d] done.' in logblob:
                logsfound = True
                break
            time.sleep(1)
        except Exception as error:
            print(error)
            remove_container(container)
            core_fail('Error getting container logs')
    if logsfound == True:
        report_tests.append(['Startup ' + tag,'PASS'])
    elif logsfound == False:
        report_tests.append(['Startup ' + tag,'FAIL INIT NOT FINISHED'])
        mark_fail()
    # Sleep for the user specified amount of time
    time.sleep(int(testdelay))
    # Take a screenshot
    if ssl == 'true':
        proto = 'https://'
    else:
        proto = 'http://'
    container.reload()
    ip = container.attrs["NetworkSettings"]["Networks"]["bridge"]["IPAddress"]
    take_screenshot(proto + ip + ':' + port ,tag)
    # Dump package information
    if base == 'alpine':
        command = 'apk info -v'
    elif base == 'debian':
        command = 'apt list'
    try:
        info = container.exec_run(command)
        packages = info[1].decode("utf-8")
        report_tests.append(['Dump Versions ' + tag,'PASS'])
    except Exception as error:
        print(error)
        report_tests.append(['Dump Versions ' + tag,'FAIL'])
        mark_fail()
    # Add the info to the report
    report_containers.append({
    "tag":tag,
    "logs":logblob,
    "sysinfo":packages
    })
    #Cleanup
    remove_container(container)


# Render the markdown file for upload
def report_render():
    with open(os.path.dirname(os.path.realpath(__file__)) + '/results.template') as file_:
        template = Template(file_.read())
    markdown = template.render(
        report_tests=report_tests,
        report_containers=report_containers,
        report_status=report_status,
        meta_tag=meta_tag,
        image=image)
    with open(outdir + 'report.md', 'w') as f:
        f.write(markdown)

# Upload report to DO Spaces
def report_upload():
    destination_dir = image + '/' + meta_tag + '/'
    spaces = session.client(
        's3',
        region_name='nyc3',
        endpoint_url='https://nyc3.digitaloceanspaces.com',
        aws_access_key_id=spaces_key,
        aws_secret_access_key=spaces_secret)
    # Index file upload
    index_file = os.path.dirname(os.path.realpath(__file__)) + '/index.html'
    try:
        spaces.upload_file(
            index_file,
            'ls-ci',
            destination_dir + 'index.html',
            ExtraArgs={'ContentType': "text/html", 'ACL': "public-read"})
    except Exception as error:
        core_fail('Upload Error ' + str(error))
    # Loop for all others
    for filename in os.listdir(outdir):
        try:
            spaces.upload_file(
                outdir + filename,
                'ls-ci',
                destination_dir + filename,
                ExtraArgs={'ACL': "public-read"})
        except Exception as error:
            core_fail('Upload Error ' + str(error))


##################
# Test Run Logic #
##################
check_env()
create_dir()
# Run through all the tags
for tag in tags:
    container_test(tag)
# Quit selenium webdriver
driver.quit()
report_render()
report_upload()
# Exit based on test results
if report_status == 'pass':
    sys.exit(0)
elif report_status == 'fail':
    sys.exit(1)
