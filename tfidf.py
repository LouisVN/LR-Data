from redis import StrictRedis
import redis
import couchdb
from pprint import pprint
import math
import mincemeat
import json
db = couchdb.Database("http://localhost:5984/lr-data")
INDEX_DB = 0
r = StrictRedis(db=INDEX_DB)

def count_map(k, v):
    yield v


def count_reduce(k, vs):    
    with open("counts/" + k, "w+") as f:
        f.write(str(sum(vs)))
    return sum(vs)


def process_keys():
    for n in xrange(ord('a'), ord('z')):
      for l in xrange(ord('a'), ord('z')):
        query = chr(n) + chr(l) + "*"
        for k in r.keys(query):
            yield k

def process_key(k):
    r = StrictRedis(db=INDEX_DB)
    try:
        p = r.pipeline()
        for (doc_id, value) in r.zrevrange(k, 0, -1, "WITHSCORES"):
            if doc_id not in db:
                print("Deleted " + doc_id + " from " + key)
                p.zrem(key, doc_id)            
                continue
            doc = db[doc_id]
            if k.lower() in doc['title'].lower() or k.lower() in doc['description'].lower():
                p.zadd(k, value, doc_id)
        print(p.exceute())
    except redis.exceptions.ResponseError:
        pass


def tfidf_reduce(args):
    if args is None:
        return
    key, doc_id, value = args
    import json
    import math
    counts = None
    r = StrictRedis(db=1)
    def freq(word, doc_id):
        return r.zscore(word, doc_id)

    def word_count(doc_id):        
        try:
            with open("counts/" + doc_id, "r+") as f:
                return float(f.read())
        except:
            return 1.0 

    def num_docs_containing(word):
        return r.zcard(word)

    def tf(word, doc_id):
        return (freq(word, doc_id) / float(word_count(doc_id)))

    def idf(word):
        return math.log(doc_count / float(num_docs_containing(word)))

    def tf_idf(word, doc_id):
        return (tf(word, doc_id) * idf(word))    
    if doc_id not in db:
        print("Deleted " + doc_id + " from " + key)
        r.zrem(key, doc_id)
        return
    doc = db[doc_id]
    multiplier = 1
    try:
        if key.lower() in doc['title'].lower():
            multiplier = 4
        elif key.lower() in doc['description'].lower():
            multiplier = 2
    except:
        pass
    rank = tf_idf(key, doc_id) * multiplier
    if rank is None :
        rank = 0
    print("{0}: {1} is {2}".format(doc_id, key, rank))
    r.zadd(key, rank, doc_id)
    return rank


from multiprocessing import Pool

p = Pool(9)

p.map(process_key, process_keys())
p.join()
