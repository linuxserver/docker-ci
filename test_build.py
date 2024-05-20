#!/usr/bin/env python3
import os
import time
from logging import Logger
from ci.ci import CI, CIError
from ci.logger import configure_logging

def run_test() -> None:
    """Run tests on container tags then build and upload reports"""
    start_time = time.time()
    ci.run(ci.tags)
    # Don't set the whole report as failed if any of the ARM tag fails.
    for tag in ci.report_containers.keys():
        if tag.startswith("amd64") and ci.report_containers[tag]['test_success'] == True:
            ci.report_status = 'PASS' # Override the report_status if an ARM tag failed, but the amd64 tag passed.
    if ci.report_status == 'PASS':
        logger.success('All tests PASSED after %.2f seconds', time.time() - start_time)

    ci.report_render()
    ci.badge_render()
    ci.json_render()
    ci.report_upload()
    if ci.report_status == 'PASS':  # Exit based on test results
        ci.log_upload()
        return
    logger.error('Tests FAILED')
    ci.log_upload()
    raise CIError('CI Tests did not PASS!')


if __name__ == '__main__':
    try:
        log_level: str = os.environ.get("CI_LOG_LEVEL","INFO")
        configure_logging(log_level)
        import logging
        logger: Logger = logging.getLogger(__name__)
        ci = CI()
        run_test()
    except Exception as err:
        logger.exception(err)
        raise CIError("I Can't Believe You've Done This!") from err
