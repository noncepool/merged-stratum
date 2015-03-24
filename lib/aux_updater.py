from twisted.internet import reactor, defer
import settings
import util
from mining.interfaces import Interfaces
import lib.logger
log = lib.logger.get_logger('aux_updater')

class AuxUpdater(object):
    def __init__(self, registry, aux_rpc):
        self.aux_rpc = aux_rpc
        self.registry = registry
        self.clock = None
        self.schedule()
                        
    def schedule(self):
        when = self._get_next_time()
        log.info("Checked for new merged work, next update in %.03f sec" % when)
        self.clock = reactor.callLater(when, self.run)
        
    def _get_next_time(self):
        when = settings.MERGED_PREVHASH_REFRESH_INTERVAL - (Interfaces.timestamper.time() - self.registry.aux_last_update) % \
               settings.MERGED_PREVHASH_REFRESH_INTERVAL
        return when  
                     
    @defer.inlineCallbacks
    def run(self):
        update = False
       
        try:
          for chain in range(len(self.aux_rpc.conns)):
            aux_block = (yield self.aux_rpc.conns[chain].getauxblock())             
            if aux_block['hash'] and aux_block['hash'] != self.registry.aux_data[chain]['hash']:
                log.info("NEW MERGED NETWORK BLOCK %s" % aux_block['hash'])
                self.registry.aux_new_block = settings.MERGED_FORCE_BLOCK_UPDATE
                update = True
                
          if update:
              self.registry.update_auxs()

        except Exception:
            log.exception("Merged UpdateWatchdog.run failed")
        finally:
            self.schedule()

