import pprint
import xmlrpc.client
import time
import requests
import base64

pretty = pprint.PrettyPrinter(4).pprint

url = "http://localhost:8069"
db = "odoo"
username = 'office@luvifermente.eu'
password = 'Afx317v72015'

common = xmlrpc.client.ServerProxy('{}/xmlrpc/2/common'.format(url))
uid = common.authenticate(db, username, password, {})
models = xmlrpc.client.ServerProxy('{}/xmlrpc/2/object'.format(url))
report = xmlrpc.client.ServerProxy('{}/xmlrpc/2/report'.format(url))

def odoo(*args):
    return models.execute_kw(db, uid, password, *args)
def report(*args):
    return report.render_report(db, uid, password, *args)

pretty(
    odoo(
        'stock.picking',
        'set_done',
        [[13]]
    )
)