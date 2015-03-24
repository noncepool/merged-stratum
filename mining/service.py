import binascii
from twisted.internet import defer

import lib.settings as settings
from stratum.services import GenericService, admin
from stratum.pubsub import Pubsub
from interfaces import Interfaces
from subscription import MiningSubscription
from lib.exceptions import SubmitException
import json
import lib.logger
log = lib.logger.get_logger('mining')
                
class MiningService(GenericService):
    '''This service provides public API for Stratum mining proxy
    or any Stratum-compatible miner software.
    
    Warning - any callable argument of this class will be propagated
    over Stratum protocol for public audience!'''
    
    service_type = 'mining'
    service_vendor = 'stratum'
    is_default = True
    event = 'mining.notify'

    @admin
    def get_server_stats(self):
        serialized = '' 
        for subscription in Pubsub.iterate_subscribers(self.event):
            try:
                if subscription != None:
                    session = subscription.connection_ref().get_session()
                    session.setdefault('authorized', {})
                    if session['authorized'].keys():
                        worker_name = session['authorized'].keys()[0]
                        difficulty = session['difficulty']
                        ip = subscription.connection_ref()._get_ip()
                        serialized += json.dumps({'worker_name': worker_name, 'ip': ip, 'difficulty': difficulty})
                    else:
                        pass
            except Exception as e:
                log.exception("Error getting subscriptions %s" % str(e))
                pass

        log.debug("Server stats request: %s" % serialized)
        return '%s' % serialized

    @admin
    def update_block(self):
        '''Connect this RPC call to 'litecoind -blocknotify' for 
        instant notification about new block on the network.
        See blocknotify.sh in /scripts/ for more info.'''
        
        log.info("NEW BLOCK NOTIFICATION RECEIVED!")
        Interfaces.template_registry.update_auxs()
        #Interfaces.template_registry.update_block()
        return True 
        
    def authorize(self, worker_name, worker_password):
        '''Let authorize worker on this connection.'''
        session = self.connection_ref().get_session()
        session.setdefault('authorized', {})
        ip = self.connection_ref()._get_ip()
        extranonce1 = session.get('extranonce1', None)

        if Interfaces.worker_manager.authorize(worker_name, worker_password):
            log.info("Worker authorized: %s IP %s" % (worker_name, str(ip)))
            session['authorized'][worker_name] = worker_password
            Interfaces.worker_manager.update_worker_diff(worker_name, settings.POOL_TARGET)
            if settings.ENABLE_WORKER_STATS:
                Interfaces.worker_manager.worker_log['authorized'][extranonce1] = (0, 0, False, Interfaces.timestamper.time())
            return True
        else:
            ip = self.connection_ref()._get_ip()
            log.info("Failed worker authorization: %s IP %s" % (worker_name, str(ip)))
            if worker_name in session['authorized']:
                del session['authorized'][worker_name]
            if extranonce1 in Interfaces.worker_manager.worker_log['authorized']:
                del Interfaces.worker_manager.worker_log['authorized'][extranonce1]
            return False
        
    def subscribe(self, *args):
        '''Subscribe for receiving mining jobs. This will
        return subscription details, extranonce1_hex and extranonce2_size'''
        
        extranonce1 = Interfaces.template_registry.get_new_extranonce1()
        extranonce2_size = Interfaces.template_registry.extranonce2_size
        extranonce1_hex = binascii.hexlify(extranonce1)
        
        session = self.connection_ref().get_session()
        session['extranonce1'] = extranonce1
        session['difficulty'] = settings.POOL_TARGET  # Following protocol specs, default diff is 1
        return Pubsub.subscribe(self.connection_ref(), MiningSubscription()) + (extranonce1_hex, extranonce2_size)
        
    def submit(self, worker_name, work_id, extranonce2, ntime, nonce):
        '''Try to solve block candidate using given parameters.'''
        
        session = self.connection_ref().get_session()
        session.setdefault('authorized', {})
        
        # Check if worker is authorized to submit shares
        ip = self.connection_ref()._get_ip()
        if not Interfaces.worker_manager.authorize(worker_name, session['authorized'].get(worker_name)):
            log.info("Worker is not authorized: %s IP %s" % (worker_name, str(ip)))
            raise SubmitException("Worker is not authorized")

        # Check if extranonce1 is in connection session
        extranonce1_bin = session.get('extranonce1', None)
        
        if not extranonce1_bin:
            log.info("Connection is not subscribed for mining: IP %s" % str(ip))
            raise SubmitException("Connection is not subscribed for mining")
        
        difficulty = session['difficulty']
        submit_time = Interfaces.timestamper.time()

        if extranonce1_bin in Interfaces.worker_manager.job_log and work_id in Interfaces.worker_manager.job_log[extranonce1_bin]:
            (job_id, difficulty, job_ts) = Interfaces.worker_manager.job_log[extranonce1_bin][work_id]
        else:
            job_ts = Interfaces.timestamper.time()
            Interfaces.worker_manager.job_log.setdefault(extranonce1_bin, {})[work_id] = (work_id, difficulty, job_ts)
            job_id = work_id
        #log.debug("worker_job_log: %s" % repr(Interfaces.worker_manager.job_log))
 
        pool_share = float(float(difficulty) * float(settings.SHARE_MULTIPLIER))

        if settings.ENABLE_WORKER_STATS:
            (valid, invalid, is_banned, last_ts) = Interfaces.worker_manager.worker_log['authorized'][extranonce1_bin]
            percent = float(float(invalid) / (float(valid) if valid else 1) * 100)

            if is_banned and submit_time - last_ts > settings.WORKER_BAN_TIME:
                if percent > settings.INVALID_SHARES_PERCENT:
                    log.info("Worker invalid percent: %0.2f %s STILL BANNED!" % (percent, worker_name))
                else: 
                    is_banned = False
                    log.info("Clearing ban for worker: %s UNBANNED" %  worker_name)
                (valid, invalid, is_banned, last_ts) = (0, 0, is_banned, Interfaces.timestamper.time())

            if submit_time - last_ts > settings.WORKER_CACHE_TIME and not is_banned:
                if percent > settings.INVALID_SHARES_PERCENT and settings.ENABLE_WORKER_BANNING:
                    is_banned = True
                    log.info("Worker invalid percent: %0.2f %s BANNED!" % (percent, worker_name))
                else:
                    log.debug("Clearing worker stats for: %s" %  worker_name)
                (valid, invalid, is_banned, last_ts) = (0, 0, is_banned, Interfaces.timestamper.time())

        if settings.ENABLE_WORKER_STATS:
            log.debug("%s (%d, %d, %s, %d) %0.2f%% job_id(%s) diff(%i) share(%i)" % (worker_name, valid, invalid, is_banned, last_ts, percent, job_id, difficulty, pool_share))

        Interfaces.share_limiter.submit(self.connection_ref, job_id, difficulty, submit_time, worker_name, extranonce1_bin)
            
        try:
            (block_header, block_hash, share_diff, on_submit) = Interfaces.template_registry.submit_share(job_id,
                worker_name, session, extranonce1_bin, extranonce2, ntime, nonce, difficulty, ip, submit_time)
        except SubmitException as e:
            # block_header and block_hash are None when submitted data are corrupted
            if settings.ENABLE_WORKER_STATS:
                invalid += 1
                if invalid > settings.INVALID_SHARES_SPAM:
                    is_banned = True
                    log.info("Worker SPAM %s BANNED! IP: %s" % (worker_name, ip))
                Interfaces.worker_manager.worker_log['authorized'][extranonce1_bin] = (valid, invalid, is_banned, last_ts)

                if is_banned:
                    raise SubmitException("Worker is temporarily banned")
 
            Interfaces.share_manager.on_submit_share(worker_name, False, False, difficulty, pool_share,
                submit_time, False, ip, e[0], 0, job_id)   
            raise

        if settings.ENABLE_WORKER_STATS:
            valid += 1
            Interfaces.worker_manager.worker_log['authorized'][extranonce1_bin] = (valid, invalid, is_banned, last_ts)

            if is_banned:
                raise SubmitException("Worker is temporarily banned")

        Interfaces.share_manager.on_submit_share(worker_name, block_header,
            block_hash, difficulty, pool_share, submit_time, True, ip, '', share_diff, job_id)

        if on_submit != None:
            on_submit.addCallback(Interfaces.share_manager.on_submit_block,
                worker_name, block_header, block_hash, submit_time, ip, share_diff)
            
        return True
        
