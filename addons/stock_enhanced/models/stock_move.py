from threading import Timer
import logging
import requests
import json
import time
import os
import datetime
from woocommerce import API


from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import UserError
from odoo.osv import expression
from odoo.tools.float_utils import float_compare, float_is_zero, float_repr, float_round
from odoo.tools.misc import format_date, OrderedSet
from odoo.api import Environment as env

from odoo.addons.stock.models.stock_move import StockMove as OStockMove
from odoo.addons.stock.models.stock_quant import StockQuant as OStockQuant
from odoo.addons.stock.models.stock_production_lot import ProductionLot as OProductionLot



_logger = logging.getLogger(__name__)

def log(*text):
    text = " ".join([str(t) for t in text])
    _logger.error(text)

with open(os.path.dirname(os.path.abspath(__file__)) + "/credentials.json", "r") as file:
    credentials = json.loads(file.read())

shops = {}
for shop in credentials["shops"]:
    if shop["url"] == "https://staging.fermentationculture.eu":
        continue
    shops[shop["url"]] = API(
        url=shop["url"],
        consumer_key=shop["consumer_key"],
        consumer_secret=shop["consumer_secret"],
        wp_api=True,
        version="wc/v3",
        timeout=20
        )


### Debounced, schickt den Lagerbestand.
def call_api(args):
    def call_it():
        for shop in shops:
            for arg in args:
                sku = arg[0]
                quantity_available = arg[1]
                # log(shops[shop].post("products/stock_update", data).json())
                time1 = time.time()
                product = shops[shop].get("products", params={'sku':sku}).json()
                if len(product) == 0:
                    continue
                product = product[0]
                
                if product['type'] == 'variation':
                    shops[shop].put('products/'+str(product['parent_id'])+'/variations/'+str(product['id']), {'stock_quantity': quantity_available}).json()
                else:
                    shops[shop].put('products/'+str(product['id']), {'stock_quantity': quantity_available}).json()
                log(f"It took {time.time()-time1} seconds")
    try:
        call_api.t.cancel()
    except(AttributeError):
        pass
    call_api.t = Timer(1, call_it)
    call_api.t.start()


class StockMove(models.Model):
    _inherit = "stock.picking"

    ### Schickt Lagerbestand von Produkten an die Shops bei Anlieferungen und bei abgeschlossener Produktion
    def write(self, vals):
        super().write(vals)
        if self.picking_type_id.id in [1, 8]:
            if self.state == "done":
                args = []
                for line in self.move_line_ids:
                    args.append( (line.product_id.default_code, self.product_id.product_tmpl_id.mapped('virtual_available')[0]) )
                call_api(args)


class StockQuantInherit(models.Model):
    _inherit = "stock.quant"

    ### Schickt Lagerbestand von Produkten an die Shops bei Lagerstandsverringerung und manueller Anpassung.
    def write(self, vals):
        res = super(StockQuantInherit, self).write(vals)
        if self.product_id.default_code:
            available = self.product_id.product_tmpl_id.mapped('virtual_available')[0]
            call_api( ((self.product_id.default_code, available),) )
        return res


class Lot(models.Model):
    _inherit = "stock.production.lot"

    ### Set Removal Date when only expiration date is supplied. Only happens in receipts.
    def write(self, vals):
        if not self.removal_date and vals.get('expiration_date'):
            self.removal_date = vals['expiration_date']
        res = super(Lot, self).write(vals)


class Picking(models.Model):
    _inherit = "stock.picking"

    @api.model
    def create(self, values):
        res = super(Picking, self).create(values)
        if res.partner_id.shipping_note:
            res.note = res.partner_id.shipping_note
        return res

    def get_orders(self):
        state = {
            "draft": "pending",
            "waiting": "on-hold",
            "confirmed": "processing",
            "assigned": "processing",
            "done": "completed",
            "cancel": "canceled"
            }
        def _get_address(partner_id):
            if partner_id.name:
                name = partner_id.name.split()
                if len(name) == 0:
                    first_name, last_name = "", ""
                elif len(name) == 1:
                    first_name, last_name = "", name[0]
                elif len(name) > 1:
                    first_name, last_name = name[0], name[1]
            else:
                first_name, last_name = "", ""

            return {
                "first_name": first_name,
                "last_name": last_name,
                "company": partner_id.commercial_company_name if partner_id.commercial_company_name else "",
                "address_1": partner_id.street if partner_id.street else "",
                "address_2": partner_id.street2 if partner_id.street2 else "",
                "city": partner_id.city if partner_id.city else "",
                "state": "",
                "postcode": partner_id.zip if partner_id.zip else "",
                "country": partner_id.country_id.code if partner_id.country_id.code else "",
                "email": partner_id.email if partner_id.email else "",
                "phone": partner_id.phone_sanitized if partner_id.phone_sanitized else ""
            }
        orders = []
        
        for picking in self:
            order = {
                "payment_method": "Rechnung",
                "created_via": "Odoo",
                "total": "0.00",
                "invoice_ids": [],
                "invoice_name": "",
                "line_items": [],
                "meta_data": [],
                "fee_lines": [],
                "customer_note": "",
                "website": self.env['ir.config_parameter'].sudo().get_param('web.base.url').replace("http", "https")
            }
            
            picking_id = isinstance(picking.id, int) and picking.id or getattr(picking, '_origin', False) and picking._origin.id
            if picking_id:
                picking = self.env['stock.picking'].with_context(lang='de_DE').search([('id', '=', picking_id)])
                if picking.picking_type_id.id != 2:
                    continue

                ### Number and state ###
                order["number"] = picking.name[7:]
                order["id"] = picking_id
                order["status"] = state[picking.state]
                order["scheduled_date"] = picking.scheduled_date
                if picking.state not in ["draft", "waiting", "done", "cancel"]:
                    if picking.scheduled_date.date() > datetime.date.today():
                        order["status"] = "on-hold"
                    else:
                        order["status"] = "processing"
                
                ### Order Total and Invcoice ###
                if picking.sale_id:
                    order["total"] = "{:0.2f}".format(picking.sale_id.amount_total)
                    for inv in picking.sale_id.invoice_ids:
                        order["invoice_ids"].append(inv.id)
                        order["invoice_name"] = inv.name
                ### Dates ###
                order["date_completed"] = picking.date_done.isoformat() if picking.date_done else ""
                order["date_created"] = picking.create_date.isoformat()
                order["date_modified"] = picking.write_date.isoformat()
                ### Address ###
                order["shipping"] = _get_address(picking.partner_id)
                order["billing"] = _get_address(picking.partner_id)
                ### Products ###
                for move in picking.move_ids_without_package: 
                    item = {
                        "name": move.product_id.display_name,
                        "quantity": move.product_uom_qty,
                        "uom": move.product_uom.name,
                        "quantity_available": move.forecast_availability,
                        "sku": move.product_id.default_code if move.product_id.default_code else "",
                        "meta_data": [],
                        "move_line_ids": len(move.move_line_ids)
                    }
                    for line in move.move_line_ids:
                        if line.lot_id:
                            item["meta_data"].append({
                                "key": "_batch_id",
                                "value": line.lot_id.name
                            })
                            item["meta_data"].append({
                                "key": "Charge",
                                "value": line.lot_id.name
                            })
                            if line.lot_id.removal_date:
                                item["meta_data"].append({
                                    "key": "_date_expiry",
                                    "value": line.lot_id.removal_date.isoformat()
                                })
                                item["meta_data"].append({
                                    "key": "MHD",
                                    "value": line.lot_id.removal_date.strftime("%d.%m.%Y")
                                })

                    order["line_items"].append(item)


                ### Meta Data ###
                order["meta_data"].append({"key": "_order_number", "value": order["number"]})
                order["meta_data"].append({"key": "order_notes", "value": picking.note if picking.note else ""})
                if picking.carrier_tracking_ref:
                    order["meta_data"].append({"key": "_tracking_code", "value": picking.carrier_tracking_ref})
            
            orders.append(order)

        return orders

    def set_done(self):
        for picking in self:
            for line in picking.move_line_ids:
                line.qty_done = line.product_qty
            picking.button_validate()
        return picking.state