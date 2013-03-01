import couchdb
import pyes
from pymongo import Connection
from celery.task import task
from celery.log import get_default_logger
import redis
import requests
from neo4jrestclient.client import GraphDatabase
from pycassa.pool import ConnectionPool
from pycassa.columnfamily import ColumnFamily
from BeautifulSoup import BeautifulSoup
from urlparse import urlparse
import hashlib
from lxml import etree
from StringIO import StringIO
import subprocess
import os
log = get_default_logger()
dc_namespaces = {
                "nsdl_dc": "http://ns.nsdl.org/nsdl_dc_v1.02/",
                "dc": "http://purl.org/dc/elements/1.1/",
                "dct": "http://purl.org/dc/terms/"
                }


@task
def insertDocumentMongo(envelope, config):
    try:
        conf = config['mongodb']
        con = Connection(conf['host'], conf['port'])
        db = con[conf['database']]
        collection = db[conf['collection']]
        del envelope['_rev']
        del envelope['_id']
        collection.insert(envelope)
    except (Exception) as exc:
        log.error(exc)
        log.error("Error writing to mongo")


@task
def insertDocumentCouchdb(envelope, config):
    try:
        conf = config['couchdb']
        db = couchdb.Database(conf['dbUrl'])
        del envelope['_rev']
        del envelope['_id']
        db.save(envelope)
    except (Exception), exc:
        log.error(exc)
        log.error("Error writing to mongo")


@task
def insertDocumentElasticSearch(envelope, config):
        r = config['redis']
        r = redis.StrictRedis(host=r['host'], port=r['port'], db=r['db'])
        count = r.incr('esid')
        conf = config['elasticsearch']
        es = pyes.ES("{0}:{1}".format(conf['host'], conf['port']))
        index = {
                    "resource_locator": envelope['resource_locator'],
                    'resource_data': envelope['resource_data'],
                    'doc_ID': envelope['doc_ID']
                 }
        es.index(index, conf['index'], conf['index-type'], count)


@task
def insertDocumentSolr(envelope, config):
        pass


@task
def insertLRInterface(envelope, config):
    if 'keys' in envelope:
        for k in envelope['keys']:
            saveToNeo.delay(k, config)
            title = envelope['resource_locator']
            try:
                headers = requests.head(title)
                if headers.headers['content-type'] == 'text/html':
                    fullPage = requests.get(title)
                    soup = BeautifulSoup(fullPage.content)
                    title = soup.html.head.title.string
            except Exception:
                pass  # expected for invalid URLs
            cassandra_data = dict(resource_url=envelope['resource_locator'],
                                  doc_id=envelope['doc_ID'],
                                  submitter=envelope['identity']['submitter'],
                                  keyword=k)
            cassandra_data['title'] = title
            saveToCassandra.delay(cassandra_data, config)

    else:
        print(envelope)


@task
def saveToCassandra(data, config):
    r = redis.StrictRedis(host=config['redis']['host'],
                          port=config['redis']['port'],
                          db=config['redis']['db'])
    pool = ConnectionPool('lr', server_list=['localhost', '10.10.1.47'])
    cf = ColumnFamily(pool, 'contentobjects')
    cassandra_id = r.incr('cassandraid')
    cf.insert(cassandra_id, data)


@task
def saveToNeo(keyword, config):
    r = redis.StrictRedis(host=config['redis']['host'],
                          port=config['redis']['port'],
                          db=config['redis']['db'])
    gdb = GraphDatabase("http://localhost:7474/db/data/")
    if not r.sismember('topics', keyword):
        r.sadd('topics', keyword)
        gdb.nodes.create(**{"email": keyword, "topic": True})


@task
def createRedisIndex(data, config):
    r = redis.StrictRedis(host=config['redis']['host'],
                          port=config['redis']['port'],
                          db=config['redis']['db'])
    parts = urlparse(data['resource_locator'])
    process_keywords(r, data)
    m = hashlib.md5()
    m.update(data['resource_locator'])
    couchdb_id = m.hexdigest()
    conf = config['couchdb']
    db = couchdb.Database(conf['dbUrl'])
    if couchdb_id not in db:
        save_display_data(parts, data, config)
        save_image(data, config)


def process_keywords(r, data):
    m = hashlib.md5()
    m.update(data['resource_locator'])
    url_hash = m.hexdigest()

    def save_to_index(k, value):
        keywords = k.split(' ')
        keywords.append(k)
        for keyword_part in keywords:
            if not r.zadd(keyword_part, 1.0, value):
                r.zincrby(keyword_part, value, 1.0)

    for k in (key.lower() for key in data['keys']):
        save_to_index(k, url_hash)
    if 'nsdl_dc' in data['payload_schema']:
        try:
            s = StringIO(data['resource_data'])
            tree = etree.parse(s)
            result = tree.xpath('/nsdl_dc:nsdl_dc/dc:subject',
                                namespaces=dc_namespaces)
            for subject in result:
                save_to_index(subject.text.lower(), url_hash)
        except etree.XMLSyntaxError:
            print(data['resource_data'])


def handle_common_core(tree, config, url):
    r = redis.StrictRedis(host=config['redis']['host'],
                          port=config['redis']['port'],
                          db=config['redis']['db'])
    query = "/nsdl_dc:nsdldc/dct:conformsTo"
    m = hashlib.md5()
    m.update(url)
    url_hash = m.hexdigest()
    result = tree.query(query, namespaces=dc_namespaces)
    for standard in result:
        r.incr(result.text)
        if not r.zadd(result.text, 1.0, url_hash):
            r.zincrby(result.text, url_hash, 1.0)


def save_display_data(parts, data, config):
    title = data['resource_locator']
    description = ""
    publisher = None
    m = hashlib.md5()
    m.update(data['resource_locator'])
    couchdb_id = m.hexdigest()
    conf = config['couchdb']
    db = couchdb.Database(conf['dbUrl'])
    try:
        headers = requests.head(data['resource_locator'])
        if 'nsdl_dc' in data['payload_schema']:
            try:
                s = StringIO(data['resource_data'])
                tree = etree.parse(s)
                result = tree.xpath('/nsdl_dc:nsdl_dc/dc:title',
                                    namespaces=dc_namespaces)
                title = result[0].text
                result = tree.xpath('/nsdl_dc:nsdl_dc/dc:description',
                                    namespaces=dc_namespaces)
                description = result[0].text
                result = tree.xpath('/nsdl_dc:nsdl_dc/dc:publisher',
                                    namespaces=dc_namespaces)
                publisher = result[0].text
                handle_common_core(tree, config, data['resource_locator'])
            except etree.XMLSyntaxError:
                print(data['resource_data'])
        elif "LRMI" in data['payload_schema']:
            metadata = data['resource_data']['items'][0]['properties']
            title = metadata.get("name", [""]).pop()
            description = metadata.get("description", [""]).pop()
            publisher = metadata.get("publisher", [""]).pop()["name"]
        elif headers.headers['content-type'].startswith('text/html'):
            fullPage = requests.get(data['resource_locator'])
            soup = BeautifulSoup(fullPage.content)
            title = soup.html.head.title.string
        else:
            title = "{0}/...{1}".format(parts.netloc,
                                        parts.path[parts.path.rfind('/'):])
        if publisher is None:
            curator = data['identity'].get("curator", None)
            owner = data['identity'].get("owner", None)
            if curator is not None and owner is not None and curator != owner:
                publisher = "{0}, supported by {1}".format(curator, owner)
            elif curator is not None:
                publisher = curator
            else:
                signer = data['identity'].get("signer", "")
                submitter = data['identity'].get("submitter", "")
                if len(signer) > len(submitter):
                    publisher = signer
                elif len(submitter) > len(signer):
                    publisher = submitter
                else:
                    publisher = ""
    except Exception as e:
        print(e)
    try:
        doc = {"_id": couchdb_id}
        if couchdb_id in db:
            doc = db[couchdb_id]
        doc["title"] = title
        doc["description"] = description
        doc["url"] = data['resource_locator']
        doc['publisher'] = publisher
        print(doc)
        db[couchdb_id] = doc
        print(doc)
        print("saved to couch")
    except Exception as ex:
        print(ex)


@task
def save_image(envelope, config):
    m = hashlib.md5()
    m.update(envelope['resource_locator'])
    couchdb_id = m.hexdigest()
    print('get lock')
    p = subprocess.Popen(" ".join(["xvfb-run", "--auto-servernum", "--server-num=1", "python", "screenshots.py", envelope['resource_locator'], couchdb_id]), shell=True, cwd=os.getcwd(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    filename = p.communicate()
    print(filename)
    print(couchdb_id)
    db = couchdb.Database(config['couchdb']['dbUrl'])
    try:
        with open(os.path.join(os.getcwd(), couchdb_id+"-thumbnail.jpg"), "rb") as f:
            db.put_attachment(db[couchdb_id], f, "thumbnail.jpeg", "image/jpeg")
            print("uploaded")
    except IOError as e:
        log.debug(os.path.join(os.getcwd(), couchdb_id+"-thumbnail.jpg"))
        log.exception(e)
    try:
        with open(os.path.join(os.getcwd(), couchdb_id+"-screenshot.jpg"), "rb") as f:
            db.put_attachment(db[couchdb_id], f, "screenshot.jpeg", "image/jpeg")
            print("uploaded")
    except IOError as e:
        log.exception(e)
    for file_to_delete in [couchdb_id+"-thumbnail.jpg", couchdb_id+"-screenshot.jpg", couchdb_id + ".jpg"]:
        try:
            os.remove(os.path.join(os.getcwd(), file_to_delete))
        except OSError as e:
            log.error(os.path.join(os.getcwd(), file_to_delete))
            print(e)
