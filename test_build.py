#!/usr/bin/env python3
from ci.ci import CI


def run_test(ci: CI):
    """Run tests on container tags and build and upload reports"""
    logger = ci.logger
    for tag in ci.tags:  # Run through all the tags
        ci.container_test(tag)
    ci.report_render()
    ci.badge_render()
    ci.report_upload()
    if ci.report_status == 'PASS':  # Exit based on test results
        logger.info('Tests Passed exiting')
        logger.info(ci.report_tests)
        ci.log_upload()
        return
    if ci.report_status == 'FAIL':
        logger.error('Tests Failed exiting')
        ci.log_upload()
        return


if __name__ == '__main__':
    ci = CI()
    logger = ci.logger
    try:
        run_test(ci)
    except Exception as err:
        logger.exception(f"{err}\nI Can't Believe You've Done This")
