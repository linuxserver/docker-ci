from multiprocessing.pool import Pool
import sys

from ci.ci import CI

ci = CI()
# Run through all the tags
pool=Pool(processes=3)
r = pool.map_async(ci.container_test, ci.tags)
r.wait()
ci.report_render()
ci.badge_render()
ci.report_upload()
# Exit based on test results
if ci.report_status == 'PASS':
    print('Tests Passed exiting 0')
    sys.exit(0)
elif ci.report_status == 'FAIL':
    print('Tests Failed exiting 1')
    sys.exit(1)
