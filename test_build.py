#!/usr/bin/env python3
import os

from ci.ci import CI, CIError
from ci.logger import configure_logging

def run_test():
    """Run tests on container tags then build and upload reports"""
    ci.run(ci.tags)
    ci.report_render()
    ci.badge_render()
    ci.json_render()
    ci.report_upload()
    if ci.report_status == 'PASS':  # Exit based on test results
        logger.info('Tests PASSED')
        ci.log_upload()
        return
    logger.error('Tests FAILED')
    ci.log_upload()
    raise CIError('CI Tests did not PASS!')


if __name__ == '__main__':
    try:
        log_level = os.environ.get("CI_LOG_LEVEL","INFO")
        configure_logging(log_level)
        import logging
        logger = logging.getLogger(__name__)
        ci = CI()
        run_test()
    except Exception as err:
        logger.exception(err)
        raise CIError("I Can't Believe You've Done This!") from err
