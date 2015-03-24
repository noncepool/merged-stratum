import StringIO
import binascii
import struct
from util import doublesha
import util
import merkletree
import halfnode
from coinbasetx import CoinbaseTransaction
import lib.logger
log = lib.logger.get_logger('block_template')

import pack
import random

# Remove dependency to settings, coinbase extras should be
# provided from coinbaser
import settings

class BlockTemplate(halfnode.CBlock):
    '''Template is used for generating new jobs for clients.
    Let's iterate extranonce1, extranonce2, ntime and nonce
    to find out valid coin block!'''
    
    coinbase_transaction_class = CoinbaseTransaction
    
    def __init__(self, timestamper, coinbaser, job_id):
        super(BlockTemplate, self).__init__()
        
        self.job_id = job_id 
        self.timestamper = timestamper
        self.coinbaser = coinbaser
        
        self.prevhash_bin = '' # reversed binary form of prevhash
        self.prevhash_hex = ''
        self.timedelta = 0
        self.curtime = 0
        self.target = 0
        self.merkletree = None
                
        self.broadcast_args = []
        self.submits = []

        self.auxs = []
                
    def fill_from_rpc(self, data, aux_data):
        '''Convert getblocktemplate result into BlockTemplate instance'''

        self.auxs = aux_data
        self.tree, self.merkle_size = util.make_auxpow_tree(aux_data)
        self.aux_targets = [None for i in self.auxs]
        merkle_leaves = [ ('0' * 64) for x in range(self.merkle_size) ]

        for chain in range(len(self.auxs)):
            merkle_index = self.tree[self.auxs[chain]['chainid']]
            merkle_leaves[merkle_index] = self.auxs[chain]['hash']
            target = self.auxs[chain]['target'].decode('hex')[::-1].encode('hex')
            self.aux_targets[chain] = int(target, 16)
            log.info("Merged Chain: %i network difficulty: %s" % (self.auxs[chain]['chainid'], float(util.diff_to_target(self.aux_targets[chain]))))

        self.merkle_hashes = [ int(t, 16) for t in merkle_leaves ]
        self.mm_data = '\xfa\xbemm' + util.aux_pow_coinbase_type.pack(dict(
            merkle_root = util.merkle_hash(self.merkle_hashes),
            size = self.merkle_size,
            nonce = 0,
        ))

        txhashes = [None] + [ util.ser_uint256(int(t['hash'], 16)) for t in data['transactions'] ]
        mt = merkletree.MerkleTree(txhashes)

        coinbase = CoinbaseTransaction(self.timestamper, self.coinbaser, data['coinbasevalue'],
                                              data['coinbaseaux']['flags'], data['height'],
                                              settings.COINBASE_EXTRAS + self.mm_data, data['curtime'])
            
        self.height = data['height']
        self.nVersion = data['version']
        self.hashPrevBlock = int(data['previousblockhash'], 16)
        self.nBits = int(data['bits'], 16)
        self.hashMerkleRoot = 0
        self.nTime = 0
        self.nNonce = 0
        self.vtx = [ coinbase, ]
        
        for tx in data['transactions']:
            t = halfnode.CTransaction()
            t.deserialize(StringIO.StringIO(binascii.unhexlify(tx['data'])))
            self.vtx.append(t)
            
        self.curtime = data['curtime']
        self.timedelta = self.curtime - int(self.timestamper.time()) 
        self.merkletree = mt
        self.target = util.uint256_from_compact(self.nBits)
        log.info("MainNet Block Height: %i network difficulty: %s" % (self.height, float(util.diff_to_target(self.target))))

        # Reversed prevhash
        self.prevhash_bin = binascii.unhexlify(util.reverse_hash(data['previousblockhash']))
        self.prevhash_hex = "%064x" % self.hashPrevBlock
        
        self.broadcast_args = self.build_broadcast_args()
                
    def register_submit(self, extranonce1, extranonce2, ntime, nonce):
        '''Client submitted some solution. Let's register it to
        prevent double submissions.'''
        
        t = (extranonce1, extranonce2, ntime, nonce)
        if t not in self.submits:
            self.submits.append(t)
            return True
        return False
            
    def build_broadcast_args(self):
        '''Build parameters of mining.notify call. All clients
        may receive the same params, because they include
        their unique extranonce1 into the coinbase, so every
        coinbase_hash (and then merkle_root) will be unique as well.'''
        job_id = self.job_id
        prevhash = binascii.hexlify(self.prevhash_bin)
        (coinb1, coinb2) = [ binascii.hexlify(x) for x in self.vtx[0]._serialized ]
        merkle_branch = [ binascii.hexlify(x) for x in self.merkletree._steps ]
        version = binascii.hexlify(struct.pack(">i", self.nVersion))
        nbits = binascii.hexlify(struct.pack(">I", self.nBits))
        ntime = binascii.hexlify(struct.pack(">I", self.curtime))
        clean_jobs = True
        
        return (job_id, prevhash, coinb1, coinb2, merkle_branch, version, nbits, ntime, clean_jobs)

    def serialize_coinbase(self, extranonce1, extranonce2):
        '''Serialize coinbase with given extranonce1 and extranonce2
        in binary form'''
        (part1, part2) = self.vtx[0]._serialized
        return part1 + extranonce1 + extranonce2 + part2
    
    def check_ntime(self, ntime):
        '''Check for ntime restrictions.'''
        if ntime < self.curtime:
            return False
        
        if ntime > (self.timestamper.time() + 7200):
            # Be strict on ntime into the near future
            # may be unnecessary
            return False
        
        return True

    def serialize_header(self, merkle_root_int, ntime_bin, nonce_bin):
        '''Serialize header for calculating block hash'''
        r  = struct.pack("<i", self.nVersion)
        r += util.ser_uint256(self.hashPrevBlock)
        r += util.ser_uint256(merkle_root_int)
        r += ntime_bin[::-1]
        r += struct.pack("<I", self.nBits)
        r += nonce_bin[::-1]    
        return r   

    def finalize(self, merkle_root_int, extranonce1_bin, extranonce2_bin, ntime, nonce):
        '''Take all parameters required to compile block candidate.
        self.is_valid() should return True then...'''
        
        self.hashMerkleRoot = merkle_root_int
        self.nTime = ntime
        self.nNonce = nonce
        self.vtx[0].set_extranonce(extranonce1_bin + extranonce2_bin)        
        self.sha256 = None # We changed block parameters, let's reset sha256 cache

