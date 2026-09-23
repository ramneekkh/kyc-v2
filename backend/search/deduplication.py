# backend/search/deduplication.py
import re
import logging
import base64
import json
import hashlib
import struct

# Constants for MinHash
NUM_PERM = 128
# We use a fixed set of seeds for reproducibility/determinism across runs if needed.
# Or just use a simple hash function with salt.
# To emulate k permutations, we can hash the item k times with different salts.

class SimpleMinHash:
    def __init__(self, num_perm=NUM_PERM, values=None):
        self.num_perm = num_perm
        if values:
            self.hashvalues = values
        else:
            # Initialize with max possible value (using 32-bit int max)
            self.hashvalues = [4294967295] * num_perm

    def update(self, b):
        """Update with a bytes object."""
        # We need to simulate k distinct hash functions.
        # A common trick is h_i(x) = (a*x + b) % c, or just hash(salt + x).
        # We'll use sha1 + salt for reasonably good distribution, or something faster.
        # For simplicity and stdlib only:
        # We can use one good hash (SHA1) and split it? No, SHA1 is 160 bits (5 ints).
        # We need 128 ints.
        # Let's use a simple linear hash generator seeded by the input.
        
        # Actually, simpler:
        # h = metadata-invariant hash of input string.
        # We want to hash the shingle 's'.
        # For i in 0..k-1: 
        #   hv = hash(s + i)
        #   self.hashvalues[i] = min(self.hashvalues[i], hv)
        
        # Optimization: Use CRC32 or similar? zlib.crc32 IS available.
        import zlib
        
        hv1 = zlib.crc32(b) & 0xffffffff
        hv2 = zlib.crc32(b + b'salt') & 0xffffffff
        
        # Double Hashing strategy to generate k hashes from 2: h_i = (h1 + i*h2) % P
        # Mersenne prime 2^31 - 1 = 2147483647 (Standard for MinHash)
        _mersenne_prime = (1 << 31) - 1
        
        for i in range(self.num_perm):
            # We treat values as unsigned 32-bit
            # But python ints are arbitrary precision.
            # We want collisions to be stable.
            
            # Use formula: (hv1 + i * hv2) % _mersenne_prime
            # But ensure result > 0
            phv = (hv1 + i * hv2) % _mersenne_prime
            if phv < self.hashvalues[i]:
                self.hashvalues[i] = phv

    def jaccard(self, other):
        """Compute estimated Jaccard similarity."""
        if not other or len(self.hashvalues) != len(other.hashvalues):
            return 0.0
        
        matches = 0
        for v1, v2 in zip(self.hashvalues, other.hashvalues):
            if v1 == v2:
                matches += 1
        return matches / float(self.num_perm)

    def serialize(self):
        """Serialize to base64 string (JSON list of ints)."""
        return json.dumps(self.hashvalues)

    @classmethod
    def deserialize(cls, s):
        values = json.loads(s)
        return cls(num_perm=len(values), values=values)


class SyndicationFilter:
    def __init__(self, threshold=0.7, num_perm=128):
        self.threshold = threshold
        self.num_perm = num_perm
        self.seen_items = [] # List of (finding_id, SimpleMinHash)

    def _shingle_text(self, text):
        """Breaks text into set of 3-word shingles."""
        if not text:
            return set()
        text = re.sub(r'[^\w\s]', '', text.lower())
        tokens = text.split()
        if len(tokens) < 3:
            return set([text])
        shingles = set()
        for i in range(len(tokens) - 2):
            shingles.add(f"{tokens[i]} {tokens[i+1]} {tokens[i+2]}")
        return shingles

    def compute_minhash(self, text):
        m = SimpleMinHash(num_perm=self.num_perm)
        shingles = self._shingle_text(text)
        for s in shingles:
            m.update(s.encode('utf-8'))
        return m

    def check_syndication_by_hash(self, finding_id, incoming_hash):
        if not incoming_hash:
            return False, None
            
        # Brute force linear scan (sufficient for subject-level scale)
        for fid, existing_mh in self.seen_items:
            score = incoming_hash.jaccard(existing_mh)
            if score >= self.threshold:
                return True, fid
        
        # No match found, add to seen
        self.seen_items.append((finding_id, incoming_hash))
        return False, None

    def serialize_minhash(self, minhash_obj):
        if not minhash_obj: return None
        return minhash_obj.serialize()

    def deserialize_minhash(self, str_val):
        if not str_val: return None
        try:
            return SimpleMinHash.deserialize(str_val)
        except:
            return None

    def preload_hashes(self, hashes_dict):
        """
        hashes_dict: {finding_id: serialized_str}
        """
        for fid, s_val in hashes_dict.items():
            mh = self.deserialize_minhash(s_val)
            if mh:
                self.seen_items.append((fid, mh))

def compute_url_hash(url):
    """Normalized URL hash (SHA-256)."""
    if not url: return None
    normalized = url.split('?')[0].strip().lower().rstrip('/')
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()
