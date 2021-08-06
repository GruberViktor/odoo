# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

{
    'name': 'Inventory Enhanced',
    'version': '0.1',
    'summary': '-',
    'description': "Verbesserte Funktionen",
    'depends': ['stock', 'product', 'barcodes', 'digest', 'base',],
    'category': 'Inventory/Inventory',
    'data': [
        'report/mrp_production_templates.xml',
        'views/mail_notification_paynow.xml',
        'views/res_partner_views.xml'
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}