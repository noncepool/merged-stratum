from time import sleep, time
import traceback
import lib.settings as settings
import lib.logger
log = lib.logger.get_logger('work_log_pruner')

def _WorkLogPruner_I(wl):
    now = time()
    pruned = 0
    deleted = 0
    if len(wl) > 0:
        for k, v in wl.items():
            if k != 'None' and not v:
                del wl[k]
                deleted += 1
    for username in wl:
        userwork = wl[username]
        for wli in tuple(userwork.keys()):
            if now > userwork[wli][2] + settings.WORK_EXPIRE:
                del userwork[wli]
                pruned += 1
    log.info('Pruned %d jobs, Deleted %d idle workers' % (pruned, deleted))

def WorkLogPruner(wl):
    while True:
        try:
            sleep(60)
            _WorkLogPruner_I(wl)
        except:
            log.debug(traceback.format_exc())
