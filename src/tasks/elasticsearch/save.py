from celery.task import task
from celery.log import get_default_logger
from celery import group, chain, chord
from celeryconfig import config

import pyes

import urlparse
import urllib2
import requests
import hashlib

from BeautifulSoup import BeautifulSoup
from lxml import etree
import nltk
from nltk.stem.snowball import PorterStemmer

import traceback
import subprocess
import os
import csv


log = get_default_logger()


conn = pyes.ES(
    config['elasticsearch']['host']+":"+config['elasticsearch']['port'],
    timeout=config['elasticsearch']['timeout'],
    bulk_size=config['elasticsearch']['bulk_size']
)

INDEX_NAME = "lr"
DOC_TYPE = "lr_doc"

def md5_hash(text):
    return hashlib.md5(text).hexdigest()

def _send_doc(doc, doc_id):
    update_function = """
        if(title != null){ctx._source.title = title;}

        if(description != null){ctx._source.description = description;}

        if(publisher != null){ctx._source.publisher = publisher;}

        for(String key : keys){
          if(!ctx._source.keys.contains(key)){
            ctx._source.keys.add(key);
          }
        }
        for(String key : standards){
          if(!ctx._source.standards.contains(key)){
            ctx._source.standards.add(key);
          }
        }
        for(String key : mediaFeatures){
          if(!ctx._source.mediaFeatures.contains(key)){
            ctx._source.mediaFeatures.add(key);
          }
        }
        for(String key : accessMode){
          if(!ctx._source.accessMode.contains(key)){
            ctx._source.accessMode.add(key);
          }
        }"""

    doc['keys'] = set([x for x in doc.get('keys', []) if x is not None])

    for k, v in [('publisher', None), ('mediaFeatures', []), ('accessMode', []), ("description", None)]:
        if k not in doc:
            doc[k] = v


    if(doc['url']):
        doc['url_domain'] = urlparse.urlparse(doc['url']).netloc

    updateResponse = conn.partial_update(INDEX_NAME, DOC_TYPE, doc_id, update_function, upsert=doc, params=doc)

    print(updateResponse)



def get_html_display(url, publisher):
    try:
        resp = urllib2.urlopen(url)
        if not resp.headers['content-type'].startswith('text/html'):
            return {
                "title": url,
                "description": url,
                "publisher": publisher,
                "url" :url,
                "hasScreenshot": False
                }
        raw = resp.read()
        raw = raw.decode('utf-8')
        soup = BeautifulSoup(raw)
        title = url
        if soup.html is not None and \
                soup.html.head is not None and \
                soup.html.head.title is not None:
            title = soup.html.head.title.string


        description = None

        # search meta tags for descriptions
        for d in soup.findAll(attrs={"name": "description"}):
            print d
            if d['content'] is not None:
                description = d['content']
                break

        # should we not find a description, make one out of the first 100 non-HTML words on the site
        if description is None:
            raw = nltk.clean_html(raw)
            tokens = nltk.word_tokenize(raw)
            description = " ".join(tokens[:100])

        return {
            "title": title,
            "description": description,
            "url": url,
            "publisher": publisher,
            "hasScreenshot": False
        }

    except Exception as ex:
        return {
            "title": url,
            "description": url,
            "publisher": publisher,
            "url": url,
            "hasScreenshot": False
        }

def process_nsdl_dc(envelope, mapping):
    doc_id = md5_hash(envelope['resource_locator'])

    #parse the resource_data into an XML dom object
    dom = etree.fromstring(envelope['resource_data'])
    #dictionary containing XML namespaces and their prefixes
    dc_namespaces = {
        "nsdl_dc": "http://ns.nsdl.org/nsdl_dc_v1.02/",
         "dc": "http://purl.org/dc/elements/1.1/",
         "dct": "http://purl.org/dc/terms/"
     }
    # run an XPath query againt the dom object that pulls out all the document titles
    standards = dom.xpath('/nsdl_dc:nsdl_dc/dct:conformsTo',
                       namespaces=dc_namespaces)
    # extract a set of all the titles from the DOM elements
    standards = {elm.text[elm.text.rfind('/') + 1:].lower() for elm in standards}
    standards = (mapping.get(s, [s]) for s in standards)
    keys = envelope['keys']
    final_standards = []
    for ids in standards:
        for s in ids:
            final_standards.append(s)
    try:
        title = dom.xpath('/nsdl_dc:nsdl_dc/dc:title', namespaces=dc_namespaces)
        if title:
            title = title.pop().text
        else:
            title = envelope['resource_locator']
        description = dom.xpath('/nsdl_dc:nsdl_dc/dc:description', namespaces=dc_namespaces)
        if description:
            description = description.pop().text
        else:
            description = envelope['resource_locator']
        doc = {
            "title": title,
            'publisher': envelope['identity']['submitter'],
            'hasScreenshot': False,
            "description": description,
            "url": envelope['resource_locator'],
                "keys": keys,
                "standards": final_standards
            }
        return doc
    except Exception as ex:
        return {
            "title": url,
            "description": envelope['resource_locator'],
            'publisher': envelope['identity']['submitter'],
            "url" :url,
            "hasScreenshot": False
        }


def process_lrmi(envelope, mapping):
    #LRMI is json so no DOM stuff is needed
    url = envelope['resource_locator']
    resource_data = envelope.get('resource_data', {})
    if 'items' in resource_data:
        properties = resource_data['items'].pop().get('properties', {})
    else:
        properties = resource_data.get('properties', {})

    educational_alignment = properties.get('educationalAlignment', [{}]).pop()
    educational_alignment_properties = educational_alignment.get('properties', {})
    standards_names = educational_alignment_properties.get('targetName', [''])

    doc_id = md5_hash(envelope['resource_locator'])
    keys = []
    keys.extend(envelope['keys'])
    keys.extend(properties.get('about', []))

    standards = []
    for ids in (mapping.get(standards_name.lower(), [standards_name.lower()]) for standards_name in standards_names):
        for s in ids:
            standards.append(s)
            #client.zrem(s, envelope['doc_ID'])
            #client.zadd(s, 1, doc_id)
    identity = envelope['identity']
    if 'publisher' in properties:
        publisher = properties.get('publisher', []).pop().get('name')
    elif 'submitter' in identity and 'owner' in identity:
        publisher = "{0} on behalf of {1}".format(envelope['identity']['submitter'], envelope['identity']['owner'])
    elif 'submitter' in identity:
        publisher = identity['submitter']
    else:
        publisher = idenity.get('owenr')
    name = properties.get('name')

    if isinstance(name, list):
        name = name.pop()
        description = properties.get('description')
        if isinstance(description, list):
            description = description.pop()

    doc = {
        "url": envelope['resource_locator'],
        "keys": keys,
        "standards": standards,
        "title": name,
        "description": description,
        'hasScreenshot': False,
        'publisher': publisher
    }

    try:
        return doc
    except Exception as ex:
        traceback.print_exc()

        return {
            "title": url,
            "description": url,
            'publisher': envelope['identity']['submitter'],
            "url" :url,
            "hasScreenshot": False
            }


def process_lr_para(envelope, mapping):
    activity = envelope.get('resource_data', {}).get('activity', {})
    action = activity.get('verb', {}).get('action')
    keys = envelope['keys']
    standards = []
    if "matched" == action:
        for a in activity.get('related', []):
            standards = a.get('id', '').lower()
            standard_id = standard[standard.rfind('/') + 1:]
            for s in mapping.get(standard_id, [standard_id]):
                standards.append(s)
    try:
        submitter = envelope['identity']['submitter']
        url = envelope['resource_locator']
        doc = get_html_display(url, submitter)
        doc['keys'] = keys
        doc['standards'] = standards
        return doc
    except Exception as ex:
        traceback.print_exc()
        return {
            "title": url,
            "description": url,
            'publisher': envelope['identity']['submitter'],
            "url" :url,
            "hasScreenshot": False
            }

def process_lom(data, mapping):
    url = data['resource_locator']
    try:
        doc_id = md5_hash(url)

        base_xpath = "//lom:lom/lom:general/lom:{0}/lom:string[@language='en-us' or @language='en-gb' or @language='en']"
        namespaces = {
            "lom": "http://ltsc.ieee.org/xsd/LOM"
            }
        dom = etree.fromstring(data['resource_data'])
        keys = []
        found_titles = dom.xpath(base_xpath.format('title'),
                                 namespaces=namespaces)
        keys.extend([i.text for i in found_titles])
        found_description = dom.xpath(base_xpath.format('description'),
                                      namespaces=namespaces)
        keys.extend([i.text for i in found_description])
        found_keyword = dom.xpath(base_xpath.format('keyword'),
                                  namespaces=namespaces)
        keys.extend([i.text for i in found_keyword])
        title = data['resource_locator']
        if found_titles:
            title = found_titles.pop().text
        desc = data['resource_locator']
        if found_description:
            desc = found_description.pop().text
        doc = {
            "url": data['resource_locator'],
            "keys": keys,
            "standards": [],
            "title": title,
            "description": desc,
            'hasScreenshot': False,
            'publisher': data['identity']['submitter']
            }
        return doc
    except Exception as ex:
        traceback.print_exc()
        return {
            "title": url,
            "description": url,
            'publisher': data['identity']['submitter'],
            "url" :url,
            "hasScreenshot": False
            }


def handle_keys_json_ld(node):
    keys = []
    target_elements = ['inLanguage', 'isbn', 'provider',
                       'learningResourceType', 'keywords',
                       'educationalUse', 'author', "intendedUserRole"]
    def handle_possible_dict(data):
        if isinstance(data, dict):
            return data.get('name')
        return data
    for k in target_elements:

        if k in node:
            if isinstance(node[k], str):
                keys.append(handle_possible_dict(node[k]))
            elif isinstance(node[k], list):
                keys.extend([handle_possible_dict(k) for k in node[k]])
    if "bookFormat" in node:
        bookFormat = node['bookFormat']
        #increment the rfind result by 1 to exclude the '/' character
        f = bookFormat[bookFormat.rfind('/')+1:]
        keys.append(f)
    if "@id" in node:
        url = node['@id']
        parts = urlparse.urlparse(url)
        qs = urlparse.parse_qs(parts.query)
        if 'downloadFormat' in qs:
            if isinstance(qs['downloadFormat'], str):
                keys.append(qs['downloadFOrmat'])
            else:
                keys.extend(qs['downloadFormat'])
    return keys

def handle_standards_json_ld(node, mapping):
    standards = []
    if 'educationalAlignment' in node:
        alignments = (n['targetName'] for n in node['educationalAlignment'] if 'targetName' in n and n.get('educationalFramework','').lower().strip() == "common core state standards")
        for alignment in alignments:
            if isinstance(alignment, str):
                standards.extend(mapping.get(alignment, alignment))
            elif isinstance(alignment, list):
                for aln in alignment:
                    standards.extend(mapping.get(aln, aln))
    return standards

def get_first_or_value(data, key, test):
    if test(data[key]):
        return data[key]
    elif isinstance(data[key], list):
        return data[key].pop()

def process_json_ld_graph(graph, mapping):
    data = {}
    keys = []
    standards = []
    media_features = []
    access_mode = []
    for node in graph:
        keys.extend(handle_keys_json_ld(node))
        standards.extend(handle_standards_json_ld(node, mapping))
        if 'accessMode' in node:
            accessMode = node['accessMode']
            if isinstance(accessMode, list):
                access_mode.extend(accessMode)
            else:
                access_mode.append(accessMode)
        for feature in ['accessibilityFeature', 'mediaFeature']:
            if  feature in node:
                mediaFeature = node[feature]
                if isinstance(mediaFeature, list):
                    media_features.extend(mediaFeature)
                else:
                    media_features.append(mediaFeature)
        if '@type' in node:
            t = node['@type']
            if '/' in t:
                type_value = t[t.rfind('/')+1:].lower()
                keys.append(type_value)
            else:
                keys.append(t)
        if 'name' in node and 'title' not in data:
            data['title'] = get_first_or_value(node, 'name', lambda x: isinstance(x, str) or isinstance(x, unicode))
        if "description" in node and 'description' not in data:
            data['description'] = get_first_or_value(node, 'description', lambda x: isinstance(x, str) or isinstance(x, unicode))
        if 'publisher' in node:
            pub = get_first_or_value(node, 'publisher', lambda x: isinstance(x, str) or isinstance(x, unicode))
            if isinstance(pub, dict):
                data['publisher'] = pub.get('name', '')
            else:
                data['publisher'] = pub
    data['keys'] = keys
    data['standards'] = standards
    data['accessMode'] = set(access_mode)
    data['mediaFeatures'] = set(media_features)
    return data

def process_json_ld(envelope, mapping):
    data = {
        "keys": envelope.get('keys', []),
        "standards": [],
        'accessMode': [],
        "mediaFeatures": [],
        'url': envelope.get('resource_locator'),
        'hasScreenshot': True
        }
    payload_graph = envelope.get('resource_data', {}).get("@graph")
    if not payload_graph:
        payload_graph = [envelope.get('resource_data', {})]
    graph_data = process_json_ld_graph(payload_graph, mapping)
    for k in ['keys', 'standards', 'accessMode', 'mediaFeatures']:
        data[k].extend(graph_data.get(k, []))
    for k in ['title', 'description', 'publisher']:
        if k not in data and k in graph_data:
            data[k] = graph_data[k]
    return data

def process_generic(envelope):
    url = envelope['resource_locator']

    doc_id = md5_hash(url)

    keys = envelope['keys']
    standards = []

    try:
        doc = get_html_display(url, envelope['identity']['submitter'])
        doc['keys'] = keys
        doc['standards'] = standards
        return doc
    except Exception as ex:
        traceback.print_exc()
        return {
            "title": url,
            "description": url,
            'publisher': envelope['identity']['submitter'],
            "url" :url,
            "hasScreenshot": False
            }

STANDARDS_MAP = None

def load_standards(file_name):
    global STANDARDS_MAP

    if not STANDARDS_MAP:
        mapping = {}
        with open(file_name, 'r') as f:
            reader = csv.reader(f)
            for row in reader:
                for item in row:
                    if 'asn.jesandco.org' in item:
                        continue
                    for item2 in row:
                        if item != item2:
                            if 'asn.jesandco.org' in item2:
                                item2 = item2[item2.rfind('/')+1:]
                            else:
                                continue
                            key = item.lower()
                            if key not in mapping:
                                mapping[key] = []
                            mapping[key].append(item2.lower())

        STANDARDS_MAP = mapping

    return STANDARDS_MAP

@task(queue="save")
def insertDoc(envelope, config, validationResults = {}):

    doc_id = md5_hash(envelope.get('resource_locator'))

    # !!! save_image is effectively an empty stub, when the time comes we can call it again
    # save_image.delay(envelope.get('resource_locator'))

    #normalize casing on all the schemas in the payload_schema array, if payload_schema isn't present use an empty array
    schemas = {schema.lower() for schema in envelope.get('payload_schema', [])}
    mapping = load_standards("standards_mapping.csv")

    try:
        doc = None
        if "lr paradata 1.0" in schemas:
            doc = process_lr_para(envelope, mapping)
        elif 'nsdl_dc' in schemas:
            doc = process_nsdl_dc(envelope, mapping)
        elif 'lrmi' in schemas and not "json-ld" in schemas:
            doc = process_lrmi(envelope, mapping)
        elif "bookshare.org json-ld" in schemas:
            doc = process_json_ld(envelope, mapping)
        elif "a11y-jsonld" in schemas or "json-ld" in schemas:
            doc = process_json_ld(envelope, mapping)
        elif "lom" in schemas:
            doc = process_lom(envelope, mapping)
        else:
           doc = process_generic(envelope)

        if doc:
            doc['keys'].append(envelope.get('identity', {}).get("owner"))

            # add results from validation to doc (whitelisted, blacklisted values)
            doc.update(validationResults)
            _send_doc(doc, doc_id)
    except Exception as ex:
        traceback.print_exc()


@task(queue="image")
def save_image(url):
    doc_id = md5_hash(url)

    # p = subprocess.Popen(" ".xvfb-run", "--auto-servernum", "--server-num=1", "python", "screenshots.py", url, doc_id]), shell=True, cwd=os.getcwd(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    # filename = p.communicate()
    # print(filename)