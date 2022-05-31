#!/usr/bin/env python3

from multiprocessing.pool import Pool
import sys

from ci.ci import CI

ci = CI()
print(vars(ci))
# Run through all the tags
#pool=Pool(processes=3)
#r = pool.map_async(ci.container_test, ci.tags)
#r.wait()
for tag in ci.tags:
    ci.container_test(tag)
ci.report_render()
ci.badge_render()
ci.report_upload()
# Exit based on test results
if ci.report_status == 'PASS':
    print('Tests Passed exiting 0')
    print(ci.report_tests)
    sys.exit(0)
elif ci.report_status == 'FAIL':
    print('Tests Failed exiting 1')
    sys.exit(1)
