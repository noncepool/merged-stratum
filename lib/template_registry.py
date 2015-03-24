import weakref
import binascii
import util
import StringIO
import settings
if settings.DAEMON_ALGO == 'scrypt':
    import ltc_scrypt
elif settings.DAEMON_ALGO == 'yescrypt':
    import yescrypt_hash
elif settings.DAEMON_ALGO == 'qubit':
    import qubit_hash
else: pass
from twisted.internet import defer
from lib.exceptions import SubmitException

import lib.logger
log = lib.logger.get_logger('template_registry')
from mining.interfaces import Interfaces
from extranonce_counter import ExtranonceCounter
import lib.settings as settings
import pack

class JobIdGenerator(object):
    '''Generate pseudo-unique job_id. It does not need to be absolutely unique,
    because pool sends "clean_jobs" flag to clients and they should drop all previous jobs.'''
    counter = 0
    
    @classmethod
    def get_new_id(cls):
        cls.counter += 1
        if cls.counter % 0xffff == 0:
            cls.counter = 1
        return "%x" % cls.counter
                
class TemplateRegistry(object):
    '''Implements the main logic of the pool. Keep track
    on valid block templates, provide internal interface for stratum
    service and implements block validation and submits.'''
    
    def __init__(self, block_template_class, coinbaser, bitcoin_rpc, aux_rpc, instance_id,
                 on_template_callback, on_block_callback):
        self.prevhashes = {}
        self.jobs = weakref.WeakValueDictionary()
        
        self.extranonce_counter = ExtranonceCounter(instance_id)
        self.extranonce2_size = block_template_class.coinbase_transaction_class.extranonce_size \
                - self.extranonce_counter.get_size()
        self.coinbaser = coinbaser
        self.block_template_class = block_template_class
        self.bitcoin_rpc = bitcoin_rpc
        self.on_block_callback = on_block_callback
        self.on_template_callback = on_template_callback
        
        self.last_block = None
        self.update_in_progress = False
        self.last_update = 0
        self.last_height = None

        self.aux_rpc = aux_rpc
        self.aux_update_in_progress = False
        self.aux_new_block = False
        self.aux_last_update = 0
        self.aux_update_counter = 0
        self.aux_data = []
        
        # Create first block template on startup
        self.update_auxs()
        #self.update_block()
        
    def get_new_extranonce1(self):
        '''Generates unique extranonce1 (e.g. for newly
        subscribed connection.'''
        return self.extranonce_counter.get_new_bin()
    
    def get_last_broadcast_args(self):
        '''Returns arguments for mining.notify
        from last known template.'''
        return self.last_block.broadcast_args
        
    def add_template(self, block,block_height):
        '''Adds new template to the registry.
        It also clean up templates which should
        not be used anymore.'''
        
        prevhash = block.prevhash_hex

        if prevhash in self.prevhashes.keys() and not self.aux_new_block:
            new_block = False
        else:
            new_block = True
            self.prevhashes[prevhash] = []
        self.aux_new_block = False       
        # Blocks sorted by prevhash, so it's easy to drop
        # them on blockchain update
        self.prevhashes[prevhash].append(block)
        
        # Weak reference for fast lookup using job_id
        self.jobs[block.job_id] = block
        
        # Use this template for every new request
        self.last_block = block
        
        # Drop templates of obsolete blocks
        for ph in self.prevhashes.keys():
            if ph != prevhash:
                del self.prevhashes[ph]
                
        log.info("New template for %s" % prevhash)

        if new_block:
            # Tell the system about new block
            # It is mostly important for share manager
            self.on_block_callback(block_height)

        # Everything is ready, let's broadcast jobs!
        self.on_template_callback(new_block)

    def update_block(self):
        '''Registry calls the getblocktemplate() RPC
        and build new block template.'''
        
        if self.update_in_progress:
            # Block has been already detected
            return
        
        self.update_in_progress = True
        self.last_update = Interfaces.timestamper.time()
        
        d = self.bitcoin_rpc.getblocktemplate()
        d.addCallback(self._update_block)
        d.addErrback(self._update_block_failed)
        
    def _update_block_failed(self, failure):
        log.error(str(failure))
        self.update_in_progress = False
        
    def _update_block(self, data):
        if self.aux_data is None:
            return
        start = Interfaces.timestamper.time()
                
        template = self.block_template_class(Interfaces.timestamper, self.coinbaser, JobIdGenerator.get_new_id())
        template.fill_from_rpc(data, self.aux_data)
        self.last_height = data['height']
        self.add_template(template, data['height'])

        log.info("Update finished, %.03f sec, %d txes" % \
                    (Interfaces.timestamper.time() - start, len(template.vtx)))
        
        self.update_in_progress = False        
        return data

    def update_auxs(self):
        if self.aux_update_in_progress:
            return
        self.aux_update_in_progress = True
        self.aux_last_update = Interfaces.timestamper.time()
        self.aux_data = []
        self.aux_update_counter = 0
        for chain in range(len(self.aux_rpc.conns)):
            aux_block = self.aux_rpc.conns[chain].getauxblock()
            aux_block.addCallback(self._update_auxs)
            aux_block.addErrback(self._update_auxs_failed)

    def _update_auxs(self, data):
        self.aux_data.append(data)
        self.aux_update_counter += 1
        if self.aux_update_counter == len(self.aux_rpc.conns):
            self.aux_update_counter = 0
            self.aux_update_in_progress = False
            self.update_block()
        return data

    def _update_auxs_failed(self,failure):
        log.error(str(failure))
        self.aux_update_in_progress = False
        self.aux_update_counter = 0
    
    def diff_to_target(self, difficulty):
        '''Converts difficulty to target'''
        if settings.DAEMON_ALGO == 'scrypt':
            diff1 = 0x0000ffff00000000000000000000000000000000000000000000000000000000
        elif settings.DAEMON_ALGO == 'yescrypt':
            diff1 = 0x0000ffff00000000000000000000000000000000000000000000000000000000
        elif settings.DAEMON_ALGO == 'qubit':
            diff1 = 0x000000ffff000000000000000000000000000000000000000000000000000000
        else:
            diff1 = 0x00000000ffff0000000000000000000000000000000000000000000000000000

        return float(diff1) / float(difficulty)
    
    def get_job(self, job_id):
        '''For given job_id returns BlockTemplate instance or None'''
        try:
            j = self.jobs[job_id]
        except:
            log.info("Job id '%s' not found" % job_id)
            return None
        
        # Now we have to check if job is still valid.
        # Unfortunately weak references are not bulletproof and
        # old reference can be found until next run of garbage collector.
        if j.prevhash_hex not in self.prevhashes:
            log.info("Prevhash of job '%s' is unknown" % job_id)
            return None
        
        if j not in self.prevhashes[j.prevhash_hex]:
            log.info("Job %s is unknown" % job_id)
            return None
        
        return j
        
    def submit_share(self, job_id, worker_name, session, extranonce1_bin, extranonce2, ntime, nonce,
                     difficulty, ip, submit_time):
        '''Check parameters and finalize block template. If it leads
           to valid block candidate, asynchronously submits the block
           back to the bitcoin network.
        
            - extranonce1_bin is binary. No checks performed, it should be from session data
            - job_id, extranonce2, ntime, nonce - in hex form sent by the client
            - difficulty - decimal number from session, again no checks performed
            - submitblock_callback - reference to method which receive result of submitblock()
        '''
        
        # Check if extranonce2 looks correctly. extranonce2 is in hex form...
        if len(extranonce2) != self.extranonce2_size * 2:
            raise SubmitException("Incorrect size of extranonce2. Expected %d chars" % (self.extranonce2_size*2))
        
        # Check for job
        job = self.get_job(job_id)
        if job == None:
            raise SubmitException("Job '%s' not found" % job_id)
                
        # Check if ntime looks correct
        if len(ntime) != 8:
            raise SubmitException("Incorrect size of ntime. Expected 8 chars")

        if not job.check_ntime(int(ntime, 16)):
            raise SubmitException("Ntime out of range")
        
        # Check nonce        
        if len(nonce) != 8:
            raise SubmitException("Incorrect size of nonce. Expected 8 chars")
        
        # Check for duplicated submit
        if not job.register_submit(extranonce1_bin, extranonce2, ntime, nonce):
            log.info("Duplicate from %s, (%s %s %s %s)" % \
                    (worker_name, binascii.hexlify(extranonce1_bin), extranonce2, ntime, nonce))
            raise SubmitException("Duplicate share")
        
        # Now let's do the hard work!
        # ---------------------------
        
        # 0. Some sugar
        extranonce2_bin = binascii.unhexlify(extranonce2)
        ntime_bin = binascii.unhexlify(ntime)
        nonce_bin = binascii.unhexlify(nonce)
                
        # 1. Build coinbase
        coinbase_bin = job.serialize_coinbase(extranonce1_bin, extranonce2_bin)
        coinbase_hash = util.doublesha(coinbase_bin)
        
        # 2. Calculate merkle root
        merkle_root_bin = job.merkletree.withFirst(coinbase_hash)
        merkle_root_int = util.uint256_from_str(merkle_root_bin)
                
        # 3. Serialize header with given merkle, ntime and nonce
        header_bin = job.serialize_header(merkle_root_int, ntime_bin, nonce_bin)
    
        # 4. Reverse header and compare it with target of the user
        if settings.DAEMON_ALGO == 'scrypt':
            hash_bin = ltc_scrypt.getPoWHash(header_bin)
        elif settings.DAEMON_ALGO == 'yescrypt':
            hash_bin = yescrypt_hash.getPoWHash(header_bin)
        elif settings.DAEMON_ALGO == 'qubit':
            hash_bin = qubit_hash.getPoWHash(header_bin)
        else:
            hash_bin = util.doublesha(header_bin)

        hash_int = util.uint256_from_str(hash_bin)
        pow_hash_hex = "%064x" % hash_int
        header_hex = binascii.hexlify(header_bin)
                 
        target_user = self.diff_to_target(difficulty)
        if hash_int > target_user:
            raise SubmitException("Share is above target")

        # Mostly for debugging purposes
        target_info = self.diff_to_target(1000)
        if hash_int <= target_info:
            log.info("Yay, share with diff above 1000")

        # Algebra tells us the diff_to_target is the same as hash_to_diff
        share_diff = float(self.diff_to_target(hash_int))

        on_submit = None
        aux_submit = None
        
        block_hash_bin = util.doublesha(header_bin)
        block_hash_hex = block_hash_bin[::-1].encode('hex_codec')        

        if hash_int <= job.target:
            log.info("MAINNET BLOCK CANDIDATE! %s diff(%f/%f)" % (block_hash_hex, share_diff, self.diff_to_target(job.target)))
            job.finalize(merkle_root_int, extranonce1_bin, extranonce2_bin, int(ntime, 16), int(nonce, 16))
            
            if not job.is_valid():
                log.exception("FINAL JOB VALIDATION FAILED!")
                            
            serialized = binascii.hexlify(job.serialize())
            if settings.SOLUTION_BLOCK_HASH:
                on_submit = self.bitcoin_rpc.submitblock(serialized, block_hash_hex)
            else:
                on_submit = self.bitcoin_rpc.submitblock(serialized, pow_hash_hex)
            
            '''if on_submit:
                self.update_block()'''

        # Check auxiliary merged chains
        for chain in range(len(job.auxs)):
            if hash_int <= job.aux_targets[chain]:
                log.info("FOUND MERGED BLOCK! %s diff(%f/%f)" % (job.auxs[chain]['hash'], share_diff, self.diff_to_target(job.aux_targets[chain])))
                coinbase_hex = binascii.hexlify(coinbase_bin)
                branch_count = job.merkletree.branchCount()
                branch_hex = job.merkletree.branchHex()
                merkle_link = util.calculate_merkle_link(job.merkle_hashes, job.tree[job.auxs[chain]['chainid']])
                submission = coinbase_hex + block_hash_hex + branch_count + branch_hex + '00000000' + merkle_link + header_hex;
                aux_submit = self.aux_rpc.conns[chain].getauxblock(job.auxs[chain]['hash'], submission)
                aux_submit.addCallback(Interfaces.share_manager.on_submit_aux_block, worker_name, header_hex, job.auxs[chain]['hash'], submit_time, ip, share_diff)
                '''if aux_submit:
                    self.update_auxs()'''
            
        if settings.SOLUTION_BLOCK_HASH:
            return (header_hex, block_hash_hex, share_diff, on_submit)
        else:
            return (header_hex, pow_hash_hex, share_diff, on_submit)

