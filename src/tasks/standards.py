from celery.task import task
from celery.log import get_default_logger
log = get_default_logger()
from couchdb import Database
from redis import StrictRedis
from threading import Thread, active_count
from time import sleep
keys_to_remove = [unicode(x) for x in ['leaf', 'dcterms_language', "text",
                  'dcterms_educationLevel', 'skos_exactMatch',
                  'dcterms_description', 'dcterms_subject',
                  'asn_indexingStatus', 'asn_authorityStatus',
                  'asn_statementLabel', 'asn_statementNotation',
                  'asn_altStatementNotation', 'cls', 'asn_comment']]


def process_doc(doc, client):
    doc['count'] = 0
    doc['childCount'] = 0
    if "asn_identifier" in doc:
        if 'uri' in doc['asn_identifier']:
            doc['id'] = doc['asn_identifier']['uri'].strip()
        else:
            doc['id'] = doc['asn_identifier'].strip()
    if 'id' in doc:
        url = doc['id']
        doc['id'] = url[url.rfind("/")+1:].lower()
    if "text" in doc:
        doc['title'] = doc['text']
    for key in keys_to_remove:
        if key.strip() in doc:
            del doc[key]
    if "id" in doc:
        items = client.zrevrange(doc['id'], 0, -1)
        count = 0
        local_db = Database("http://localhost:5984/lr-data")
        for doc_id in items:
            if doc_id in local_db:
                count += 1
        doc['count'] = count    
    if "children" in doc:
        for child in doc['children']:
            doc['childCount'] += process_doc(child, client)
    return doc['count'] + doc['childCount']


@task(queue="rollup")
def rollup(config):
    def thread_start(doc_id, db, r):
        doc = db[doc_id]
        print(doc_id)
        process_doc(doc, r)
        print(db.save(doc))
    r = StrictRedis(host=config['redis']['host'],
                    port=config['redis']['port'],
                    db=config['redis']['db'])
    db = Database(config['couchdb']['standardsDb'])
    for doc_id in db:
        Thread(target=thread_start, kwargs={"doc_id": doc_id, "db": db, "r": r}).start()

    while active_count() > 1:
        sleep(1)

if __name__ == "__main__":
    config = {
        "lrUrl": "https://node01.public.learningregistry.net/harvest/listrecords",
        "mongodb": {
            "database": "lr",
            "collection": "envelope",
            "host": "localhost",
            "port": 27017,
        },
        "couchdb": {
            "dbUrl": "http://localhost:5984/lr-data",
            "standardsDb": "http://localhost:5984/standards",
        },
        'elasticsearch': {
            "host": "localhost",
            "port": 9200,
            "index": "lr",
            "index-type": "lr"
        },
        "insertTask": "tasks.save.createRedisIndex",
        "validationTask": "tasks.validate.checkWhiteList",
        "redis": {
            "host": "localhost",
            "port": 6379,
            "db": 1
        }
    }
    rollup(config)
